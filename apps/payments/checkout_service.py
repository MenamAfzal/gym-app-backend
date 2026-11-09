import hashlib
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal
import stripe
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import models, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.core.tenants.models import Tenant
from apps.payments.models import (
    CheckoutVerificationChallenge,
    ExternalCheckoutSession,
    StripeProcessedEvent,
    TenantCheckoutConfig,
)
from apps.payments.stripe_package_service import StripePackageService
from apps.scheduling.models import Location, Package, PackageGrantSource, PackageType, Payment
from apps.users.models import EmailOTP, OTPPurpose, TenantCustomerAccess, User, UserRole
from apps.users.services import UserService


def resolve_tenant_by_identifier(identifier: str) -> Tenant:
    if not identifier:
        raise NotFound('Gym identifier is required.')
    
    clean_id = str(identifier).strip()
    try:
        val_uuid = uuid.UUID(clean_id)
        tenant = Tenant.objects.filter(id=val_uuid, is_active=True).first()
    except (ValueError, AttributeError):
        tenant = Tenant.objects.filter(subdomain__iexact=clean_id, is_active=True).first()

    if not tenant:
        raise NotFound('Gym account not found or is inactive.')
    return tenant


class CheckoutIntegrationService:
    @staticmethod
    def _is_origin_allowed(origin, config):
        if not origin or not config or not config.allowed_origins:
            return True
        clean_origin = origin.rstrip('/').lower()

        allowed_system_origins = [
            str(o).rstrip('/').lower()
            for o in getattr(settings, 'CHECKOUT_ALLOWED_ORIGINS', [])
            if o
        ]
        frontend_checkout_url = getattr(settings, 'FRONTEND_CHECKOUT_URL', None)
        if frontend_checkout_url:
            allowed_system_origins.append(str(frontend_checkout_url).rstrip('/').lower())

        if clean_origin in allowed_system_origins:
            return True

        if getattr(settings, 'DEBUG', False) and ('localhost' in clean_origin or '127.0.0.1' in clean_origin):
            return True
        for allowed in config.allowed_origins:
            clean_allowed = str(allowed).rstrip('/').lower()
            if clean_origin == clean_allowed:
                return True
            if not clean_allowed.startswith('http://') and not clean_allowed.startswith('https://'):
                if clean_origin in [f'https://{clean_allowed}', f'http://{clean_allowed}']:
                    return True
        return False

    @staticmethod
    def get_public_gym_info(tenant_identifier, origin=None):
        tenant = resolve_tenant_by_identifier(tenant_identifier)
        config = getattr(tenant, 'checkout_config', None)
        if not config or not config.is_enabled:
            raise NotFound('Website checkout is not enabled for this gym.')

        if not CheckoutIntegrationService._is_origin_allowed(origin, config):
            raise PermissionDenied('Origin is not allowed.')

        return tenant

    @staticmethod
    def get_available_packages(tenant_identifier, location_id=None, origin=None):
        tenant = resolve_tenant_by_identifier(tenant_identifier)
        config = getattr(tenant, 'checkout_config', None)
        if not config or not config.is_enabled:
            raise NotFound('Website checkout is not enabled for this gym.')

        if not CheckoutIntegrationService._is_origin_allowed(origin, config):
            raise PermissionDenied('Origin is not allowed.')

        queryset = PackageType.all_objects.filter(
            tenant=tenant,
            is_active=True,
            price__gt=Decimal('0.00')
        ).select_related('location').order_by('price')

        if config.enabled_package_ids:
            allowed_ids = [str(pid) for pid in config.enabled_package_ids]
            queryset = queryset.filter(id__in=allowed_ids)

        if location_id:
            queryset = queryset.filter(location_id=location_id)

        return list(queryset)

    @staticmethod
    def create_checkout_session(validated_data, origin=None):
        tenant_identifier = validated_data.get('tenant_id')
        tenant = resolve_tenant_by_identifier(tenant_identifier)

        config = getattr(tenant, 'checkout_config', None)
        if not config or not config.is_enabled:
            raise NotFound('Website checkout is not enabled for this gym.')

        if not CheckoutIntegrationService._is_origin_allowed(origin, config):
            raise PermissionDenied('Origin is not allowed.')

        package_type_id = validated_data.get('package_type_id')
        package_type = PackageType.all_objects.filter(
            id=package_type_id,
            tenant=tenant,
            is_active=True,
            price__gt=Decimal('0.00')
        ).first()
        if not package_type:
            raise ValidationError({'package_type_id': 'Selected package is not available.'})

        if config.enabled_package_ids:
            allowed_ids = [str(pid) for pid in config.enabled_package_ids]
            if str(package_type.id) not in allowed_ids:
                raise ValidationError({'package_type_id': 'Selected package is not enabled for online checkout.'})

        email = validated_data.get('email', '').strip().lower()
        existing_user = User.objects.filter(email=email).first()
        if existing_user:
            active_sub = Package.all_objects.filter(
                tenant=tenant,
                client=existing_user,
                package_type=package_type,
                status='active'
            ).exists()
            if active_sub:
                raise ValidationError({
                    'detail': 'You already have an active membership for this package.',
                    'code': 'ALREADY_SUBSCRIBED_TO_PACKAGE'
                })

        now = timezone.now()
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

        code = ''.join(str(secrets.randbelow(10)) for _ in range(6))
        otp_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()

        expires_at = now + timedelta(minutes=30)
        otp_expires_at = now + timedelta(minutes=5)

        session = ExternalCheckoutSession.objects.create(
            tenant=tenant,
            package_type=package_type,
            token_hash=token_hash,
            email=email,
            first_name=validated_data.get('first_name', '').strip(),
            last_name=validated_data.get('last_name', '').strip(),
            phone_number=validated_data.get('phone_number', '').strip(),
            price_snapshot=package_type.price,
            currency='usd',
            billing_cycle_snapshot=package_type.billing_cycle,
            credit_count_snapshot=package_type.credit_count,
            validity_days_snapshot=package_type.validity_days,
            is_verified=False,
            otp_hash=otp_hash,
            otp_attempts=0,
            otp_expires_at=otp_expires_at,
            otp_last_sent_at=now,
            status=ExternalCheckoutSession.StatusChoices.PENDING_VERIFICATION,
            fbp=validated_data.get('fbp', '').strip() or None,
            fbc=validated_data.get('fbc', '').strip() or None,
            source_url=validated_data.get('source_url', '').strip() or None,
            expires_at=expires_at
        )

        CheckoutIntegrationService._send_checkout_otp_email(
            email=email,
            code=code,
            tenant_name=tenant.name,
            package_name=package_type.name
        )

        return {
            'session': session,
            'session_token': raw_token,
        }

    @staticmethod
    def _send_checkout_otp_email(email, code, tenant_name, package_name):
        subject = f'{tenant_name} – Your Checkout Verification Code'
        html_message = render_to_string('emails/checkout_otp.html', {
            'code': code,
            'tenant_name': tenant_name,
            'package_name': package_name,
            'current_year': timezone.now().year,
        })
        msg = EmailMultiAlternatives(
            subject=subject,
            body=strip_tags(html_message),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email]
        )
        msg.attach_alternative(html_message, 'text/html')
        msg.send(fail_silently=False)

    @staticmethod
    def authenticate_session(session_id, token):
        if not session_id or not token:
            raise PermissionDenied('Checkout session ID and token are required.')

        token_hash = hashlib.sha256(str(token).strip().encode('utf-8')).hexdigest()
        session = ExternalCheckoutSession.all_objects.select_related(
            'tenant',
            'package_type',
            'package_type__location',
            'customer_user',
            'created_package'
        ).filter(id=session_id, token_hash=token_hash).first()

        if not session:
            raise PermissionDenied('Invalid or unauthorized checkout session.')

        if session.status in [
            ExternalCheckoutSession.StatusChoices.PENDING_VERIFICATION,
            ExternalCheckoutSession.StatusChoices.READY_FOR_PAYMENT,
            ExternalCheckoutSession.StatusChoices.PAYMENT_PENDING
        ] and session.expires_at <= timezone.now():
            session.status = ExternalCheckoutSession.StatusChoices.EXPIRED
            session.save(update_fields=['status'])

        return session

    @staticmethod
    def resend_session_otp(session):
        now = timezone.now()
        if session.expires_at <= now:
            session.status = ExternalCheckoutSession.StatusChoices.EXPIRED
            session.save(update_fields=['status'])
            raise ValidationError({'detail': 'This checkout session has expired. Please create a new session.'})

        if session.otp_last_sent_at and (now - session.otp_last_sent_at).total_seconds() < 60:
            remaining = 60 - int((now - session.otp_last_sent_at).total_seconds())
            raise ValidationError({'detail': f'Please wait {remaining} seconds before requesting a new code.'})

        code = ''.join(str(secrets.randbelow(10)) for _ in range(6))
        otp_hash = hashlib.sha256(code.encode('utf-8')).hexdigest()

        session.otp_hash = otp_hash
        session.otp_attempts = 0
        session.otp_expires_at = now + timedelta(minutes=5)
        session.otp_last_sent_at = now
        session.save(update_fields=['otp_hash', 'otp_attempts', 'otp_expires_at', 'otp_last_sent_at'])

        CheckoutIntegrationService._send_checkout_otp_email(
            email=session.email,
            code=code,
            tenant_name=session.tenant.name,
            package_name=session.package_type.name
        )

        return {
            'detail': 'A new verification code has been sent to your email.',
            'expires_in_seconds': 300,
            'cooldown_seconds': 60,
        }

    @staticmethod
    def verify_session_otp(session, otp_code):
        now = timezone.now()
        if session.expires_at <= now:
            session.status = ExternalCheckoutSession.StatusChoices.EXPIRED
            session.save(update_fields=['status'])
            raise ValidationError({'detail': 'This checkout session has expired. Please create a new session.'})

        if session.is_verified:
            return session

        with transaction.atomic():
            locked = ExternalCheckoutSession.all_objects.select_for_update().filter(id=session.id).first()
            if not locked:
                raise ValidationError({'detail': 'Checkout session not found.'})
            if locked.otp_attempts >= 5:
                raise ValidationError({'detail': 'Maximum verification attempts exceeded. Please request a new code.'})

            if not locked.otp_expires_at or locked.otp_expires_at <= now:
                raise ValidationError({'detail': 'Verification code has expired. Please request a new code.'})

            code_hash = hashlib.sha256(str(otp_code).strip().encode('utf-8')).hexdigest()
            is_valid = (code_hash == locked.otp_hash)

        if not is_valid:
            ExternalCheckoutSession.all_objects.filter(id=session.id).update(
                otp_attempts=models.F('otp_attempts') + 1
            )
            session.refresh_from_db()
            if session.otp_attempts >= 5:
                raise ValidationError({'detail': 'Maximum verification attempts exceeded. Please request a new code.'})
            raise ValidationError({'detail': 'Invalid verification code.'})

        with transaction.atomic():
            locked = ExternalCheckoutSession.all_objects.select_for_update().filter(id=session.id).first()
            locked.is_verified = True
            locked.status = ExternalCheckoutSession.StatusChoices.READY_FOR_PAYMENT
            locked.save(update_fields=['is_verified', 'status'])
            return locked

    @staticmethod
    def initiate_session_payment(session, return_url=None, cancel_url=None):
        if not session.is_verified:
            raise ValidationError({'detail': 'Email verification is required before initiating payment.'})

        now = timezone.now()
        if session.expires_at <= now:
            session.status = ExternalCheckoutSession.StatusChoices.EXPIRED
            session.save(update_fields=['status'])
            raise ValidationError({'detail': 'This checkout session has expired.'})

        if session.status not in [
            ExternalCheckoutSession.StatusChoices.READY_FOR_PAYMENT,
            ExternalCheckoutSession.StatusChoices.PAYMENT_PENDING,
            ExternalCheckoutSession.StatusChoices.PAYMENT_FAILED
        ]:
            raise ValidationError({'detail': f'Cannot initiate payment for a session with status {session.status}.'})

        package_type = session.package_type
        if not package_type.stripe_price_id:
            StripePackageService.sync_package_to_stripe(package_type)

        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            stripe_price = stripe.Price.retrieve(package_type.stripe_price_id)
        except Exception as e:
            raise ValidationError({'detail': f'Unable to verify price with payment gateway: {e}'})

        expected_cents = int(session.price_snapshot * 100)
        if (
            not stripe_price.active
            or stripe_price.currency.lower() != session.currency.lower()
            or stripe_price.unit_amount != expected_cents
        ):
            StripePackageService.sync_package_to_stripe(package_type)
            stripe_price = stripe.Price.retrieve(package_type.stripe_price_id)
            if (
                not stripe_price.active
                or stripe_price.currency.lower() != session.currency.lower()
                or stripe_price.unit_amount != expected_cents
            ):
                raise ValidationError({'detail': 'Payment gateway price is out of sync with package snapshot.'})

        frontend_checkout_url = getattr(settings, 'FRONTEND_CHECKOUT_URL', '') or getattr(settings, 'FRONTEND_URL', '')
        default_success_url = f'{frontend_checkout_url}/sessions/{session.id}/status?gateway_status=success'
        default_cancel_url = f'{frontend_checkout_url}/sessions/{session.id}?gateway_status=cancel'

        final_success = return_url or default_success_url
        final_cancel = cancel_url or default_cancel_url

        stripe_session = stripe.checkout.Session.create(
            mode='subscription',
            customer_email=session.email,
            line_items=[{
                'price': package_type.stripe_price_id,
                'quantity': 1,
            }],
            success_url=final_success,
            cancel_url=final_cancel,
            metadata={
                'type': 'external_package_checkout',
                'checkout_session_id': str(session.id),
                'tenant_id': str(session.tenant_id),
                'package_type_id': str(package_type.id),
            }
        )

        session.stripe_checkout_session_id = stripe_session.id
        session.status = ExternalCheckoutSession.StatusChoices.PAYMENT_PENDING
        session.save(update_fields=['stripe_checkout_session_id', 'status'])

        from apps.payments.tasks import send_meta_capi_event_task
        send_meta_capi_event_task.delay(str(session.id), 'InitiateCheckout')

        return {
            'session_id': str(session.id),
            'gateway': 'stripe',
            'stripe_checkout_url': stripe_session.url,
            'stripe_session_id': stripe_session.id,
            'client_secret': getattr(stripe_session, 'client_secret', None),
            'status': session.status,
        }

    @staticmethod
    def set_session_customer_password(session, new_password):
        if session.status != ExternalCheckoutSession.StatusChoices.COMPLETED:
            raise ValidationError({'detail': 'Password setup is only available after payment completion.'})

        user = session.customer_user
        if not user:
            user = User.objects.filter(email=session.email).first()

        if not user:
            raise NotFound('Associated customer account not found.')

        user.set_password(new_password)
        user.save(update_fields=['password'])
        return {'detail': 'Password configured successfully. You may now sign in to the Fitverx mobile app.'}

    @staticmethod
    def fulfill_checkout_webhook(event_data):
        event_id = event_data.get('id')
        event_type = event_data.get('type')
        data_object = event_data.get('data', {}).get('object', {})

        if event_id:
            with transaction.atomic():
                already_processed = StripeProcessedEvent.objects.filter(event_id=event_id).exists()
                if already_processed:
                    return {'status': 'already_processed'}
                StripeProcessedEvent.objects.create(
                    event_id=event_id,
                    event_type=event_type or 'unknown'
                )

        metadata = data_object.get('metadata', {})
        if metadata.get('type') != 'external_package_checkout':
            return {'status': 'ignored'}

        checkout_session_id = metadata.get('checkout_session_id')
        if not checkout_session_id:
            return {'status': 'missing_checkout_session_id'}

        with transaction.atomic():
            session = ExternalCheckoutSession.all_objects.select_for_update().filter(
                id=checkout_session_id
            ).select_related('tenant', 'package_type').first()

            if not session:
                return {'status': 'session_not_found'}

            if session.status == ExternalCheckoutSession.StatusChoices.COMPLETED:
                return {'status': 'already_completed'}

            stripe_amount_total = data_object.get('amount_total')
            stripe_currency = (data_object.get('currency') or '').lower()
            expected_amount = int(session.price_snapshot * 100)

            if stripe_amount_total is not None and (
                stripe_amount_total != expected_amount or stripe_currency != session.currency.lower()
            ):
                session.status = ExternalCheckoutSession.StatusChoices.PRICE_MISMATCH
                session.save(update_fields=['status'])
                return {'status': 'price_mismatch'}

            stripe_subscription_id = data_object.get('subscription')
            existing_package = Package.all_objects.filter(
                stripe_subscription_id=stripe_subscription_id
            ).first() if stripe_subscription_id else None

            if existing_package:
                session.status = ExternalCheckoutSession.StatusChoices.COMPLETED
                session.created_package = existing_package
                session.save(update_fields=['status', 'created_package'])
                return {'status': 'package_already_exists'}

            user = User.objects.filter(email=session.email).first()
            is_new_customer = False
            if not user:
                user = UserService.create_user_with_profile(
                    email=session.email,
                    password=secrets.token_urlsafe(16),
                    role=UserRole.CLIENT,
                    tenant=session.tenant,
                    first_name=session.first_name,
                    last_name=session.last_name,
                    phone_number=session.phone_number
                )
                user.set_unusable_password()
                user.save(update_fields=['password'])
                is_new_customer = True

            TenantCustomerAccess.objects.get_or_create(
                user=user,
                tenant=session.tenant,
                defaults={'role': UserRole.CLIENT, 'is_active': True}
            )

            package_type = session.package_type
            now = timezone.now()
            expires_at = now + timedelta(days=package_type.validity_days)
            if stripe_subscription_id:
                try:
                    stripe.api_key = settings.STRIPE_SECRET_KEY
                    sub_obj = stripe.Subscription.retrieve(stripe_subscription_id)
                    current_period_end = sub_obj.get('current_period_end')
                    if current_period_end:
                        from datetime import datetime
                        expires_at = datetime.fromtimestamp(current_period_end, tz=timezone.utc)
                except Exception:
                    pass

            package = Package.objects.create(
                tenant=session.tenant,
                client=user,
                package_type=package_type,
                credits_remaining=session.credit_count_snapshot,
                total_credits_allocated=session.credit_count_snapshot,
                purchased_at=now,
                expires_at=expires_at,
                stripe_subscription_id=stripe_subscription_id,
                status='active',
                price=session.price_snapshot,
                grant_source=PackageGrantSource.PURCHASE,
                is_complimentary=False
            )

            provider_ref = data_object.get('id') or f'stripe_{session.id}'
            payment = Payment.objects.create(
                tenant=session.tenant,
                client=user,
                package=package,
                amount=session.price_snapshot,
                type='package_purchase',
                status='completed',
                provider_ref=provider_ref,
                idempotency_key=provider_ref
            )

            session.status = ExternalCheckoutSession.StatusChoices.COMPLETED
            session.customer_user = user
            session.created_package = package
            session.payment_record = payment
            session.stripe_subscription_id = stripe_subscription_id
            session.is_new_customer = is_new_customer
            session.completed_at = now
            session.save(update_fields=[
                'status',
                'customer_user',
                'created_package',
                'payment_record',
                'stripe_subscription_id',
                'is_new_customer',
                'completed_at'
            ])

        from apps.payments.tasks import send_checkout_welcome_email_task, send_meta_capi_event_task
        send_checkout_welcome_email_task.delay(str(session.id))
        send_meta_capi_event_task.delay(str(session.id), 'Purchase')

        try:
            from apps.retention.tasks import attribute_conversion_task, recalculate_single_client_metrics
            recalculate_single_client_metrics.delay(str(session.tenant_id), str(user.id))
            attribute_conversion_task.delay(str(session.tenant_id), str(user.id), 'package_purchase', str(payment.id))
        except Exception:
            pass

        return {'status': 'fulfilled', 'package_id': str(package.id)}
