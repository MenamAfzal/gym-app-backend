import hashlib
import hmac
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
    DEFAULT_HEALTH_COMMITMENT_BODY,
    DEFAULT_HEALTH_COMMITMENT_CHECKBOX_LABEL,
    DEFAULT_HEALTH_COMMITMENT_TITLE,
    DEFAULT_WELCOME_AGREEMENT_NOTE,
    DEFAULT_WELCOME_BODY_TEXT,
    DEFAULT_WELCOME_CLOSING_TEXT,
    DEFAULT_WELCOME_EMAIL_SUBJECT,
    DEFAULT_WELCOME_HEADLINE,
    DEFAULT_WELCOME_INTRO_TEXT,
    ExternalCheckoutSession,
    HealthCommitmentSignature,
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
        ).select_related('location').order_by('-is_featured', 'price')

        if config.enabled_package_ids:
            allowed_ids = [str(pid) for pid in config.enabled_package_ids]
            queryset = queryset.filter(id__in=allowed_ids)

        if location_id:
            queryset = queryset.filter(location_id=location_id)

        return list(queryset)

    @staticmethod
    def _get_health_commitment_snapshot_values(config):
        if not config:
            return (
                True,
                1,
                DEFAULT_HEALTH_COMMITMENT_TITLE,
                DEFAULT_HEALTH_COMMITMENT_BODY,
                DEFAULT_HEALTH_COMMITMENT_CHECKBOX_LABEL,
            )
        return (
            config.require_health_commitment,
            config.health_commitment_version,
            config.health_commitment_title,
            config.health_commitment_body,
            config.health_commitment_checkbox_label,
        )

    @staticmethod
    def _apply_health_commitment_to_session(session, config, signer_name='', ip_address=None, user_agent=None):
        now = timezone.now()
        _, version, title, body, checkbox_label = CheckoutIntegrationService._get_health_commitment_snapshot_values(config)
        resolved_signer = (
            (signer_name or '').strip()
            or f"{session.first_name} {session.last_name}".strip()
            or session.email
        )
        session.health_commitment_accepted = True
        session.health_commitment_accepted_at = now
        session.health_commitment_version_snapshot = version
        session.health_commitment_title_snapshot = title
        session.health_commitment_body_snapshot = body
        session.health_commitment_checkbox_label_snapshot = checkbox_label
        session.health_commitment_signer_name = resolved_signer
        session.health_commitment_ip_address = ip_address
        session.health_commitment_user_agent = user_agent

    @staticmethod
    def accept_health_commitment(session, accepted=True, signer_name='', ip_address=None, user_agent=None):
        now = timezone.now()
        if session.expires_at <= now:
            session.status = ExternalCheckoutSession.StatusChoices.EXPIRED
            session.save(update_fields=['status'])
            raise ValidationError({'detail': 'This checkout session has expired. Please create a new session.'})

        if not accepted:
            raise ValidationError({
                'detail': 'You must accept the Health Commitment Statement to proceed.',
                'code': 'HEALTH_COMMITMENT_REQUIRED'
            })

        config = getattr(session.tenant, 'checkout_config', None)
        with transaction.atomic():
            locked = ExternalCheckoutSession.all_objects.select_for_update().filter(id=session.id).first()
            if not locked:
                raise ValidationError({'detail': 'Checkout session not found.'})
            CheckoutIntegrationService._apply_health_commitment_to_session(
                locked,
                config,
                signer_name=signer_name,
                ip_address=ip_address,
                user_agent=user_agent
            )
            locked.save(update_fields=[
                'health_commitment_accepted',
                'health_commitment_accepted_at',
                'health_commitment_version_snapshot',
                'health_commitment_title_snapshot',
                'health_commitment_body_snapshot',
                'health_commitment_checkbox_label_snapshot',
                'health_commitment_signer_name',
                'health_commitment_ip_address',
                'health_commitment_user_agent',
            ])
            return {
                'session_id': str(locked.id),
                'accepted': locked.health_commitment_accepted,
                'accepted_at': locked.health_commitment_accepted_at,
                'signer_name': locked.health_commitment_signer_name,
                'version': locked.health_commitment_version_snapshot,
                'detail': 'Health Commitment Statement signed successfully.',
            }

    @staticmethod
    def create_checkout_session(validated_data, origin=None, ip_address=None, user_agent=None):
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

        session = ExternalCheckoutSession(
            tenant=tenant,
            package_type=package_type,
            token_hash=token_hash,
            email=email,
            first_name=validated_data.get('first_name', '').strip(),
            last_name=validated_data.get('last_name', '').strip(),
            phone_number=validated_data.get('phone_number', '').strip(),
            price_snapshot=package_type.price,
            currency=(tenant.currency or 'usd').lower() if tenant else 'usd',
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
            client_ip_address=ip_address,
            client_user_agent=user_agent,
            source_url=validated_data.get('source_url', '').strip() or None,
            expires_at=expires_at
        )
        if validated_data.get('health_commitment_accepted'):
            CheckoutIntegrationService._apply_health_commitment_to_session(
                session,
                config,
                signer_name=validated_data.get('health_commitment_signer_name', ''),
                ip_address=ip_address,
                user_agent=user_agent
            )
        session.save()

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
    def initiate_session_payment(
        session,
        return_url=None,
        cancel_url=None,
        health_commitment_accepted=None,
        signer_name='',
        ip_address=None,
        user_agent=None
    ):
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

        if health_commitment_accepted and not session.health_commitment_accepted:
            CheckoutIntegrationService.accept_health_commitment(
                session=session,
                accepted=True,
                signer_name=signer_name,
                ip_address=ip_address,
                user_agent=user_agent
            )
            session.refresh_from_db()

        config = getattr(session.tenant, 'checkout_config', None) if session.tenant else None
        require_hc = config.require_health_commitment if config else True
        if require_hc and not session.health_commitment_accepted:
            raise ValidationError({
                'detail': 'You must accept the Health Commitment Statement before proceeding to payment.',
                'code': 'HEALTH_COMMITMENT_REQUIRED'
            })

        package_type = session.package_type
        if not package_type.stripe_price_id:
            StripePackageService.sync_package_to_stripe(package_type)

        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            stripe_price = stripe.Price.retrieve(package_type.stripe_price_id)
        except Exception as e:
            raise ValidationError({'detail': f'Unable to verify price with payment gateway: {e}'})

        expected_cents = CheckoutIntegrationService.get_stripe_amount(session.price_snapshot, session.currency)
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
            'checkout_url': stripe_session.url,
            'stripe_checkout_url': stripe_session.url,
            'stripe_session_id': stripe_session.id,
            'publishable_key': getattr(settings, 'STRIPE_PUBLISHABLE_KEY', None) or getattr(settings, 'STRIPE_PUBLIC_KEY', None),
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
            expected_amount = CheckoutIntegrationService.get_stripe_amount(session.price_snapshot, session.currency)

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

            if session.health_commitment_accepted:
                config = getattr(session.tenant, 'checkout_config', None) if session.tenant else None
                _, version, title, body, checkbox_label = CheckoutIntegrationService._get_health_commitment_snapshot_values(config)
                signer_name = (
                    session.health_commitment_signer_name
                    or f"{session.first_name} {session.last_name}".strip()
                    or session.email
                )
                HealthCommitmentSignature.all_objects.get_or_create(
                    checkout_session=session,
                    defaults={
                        'tenant': session.tenant,
                        'user': user,
                        'signer_name': signer_name,
                        'signer_email': session.email,
                        'statement_version': session.health_commitment_version_snapshot or version,
                        'statement_title': session.health_commitment_title_snapshot or title,
                        'statement_body': session.health_commitment_body_snapshot or body,
                        'checkbox_label': session.health_commitment_checkbox_label_snapshot or checkbox_label,
                        'accepted': True,
                        'signed_at': session.health_commitment_accepted_at or now,
                        'ip_address': session.health_commitment_ip_address or session.client_ip_address,
                        'user_agent': session.health_commitment_user_agent or session.client_user_agent,
                        'source': 'website_checkout',
                    }
                )

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

    ZERO_DECIMAL_CURRENCIES = {
        'bif', 'clp', 'djf', 'gnf', 'jpy', 'kmf', 'krw', 'mga',
        'pyg', 'rwf', 'ugx', 'vnd', 'vuv', 'xaf', 'xof', 'xpf',
    }

    CURRENCY_SYMBOLS = {
        'usd': '$',
        'eur': '€',
        'gbp': '£',
        'cad': 'CA$',
        'aud': 'A$',
        'nzd': 'NZ$',
        'chf': 'CHF ',
        'aed': 'AED ',
        'sar': 'SAR ',
        'sgd': 'S$',
        'hkd': 'HK$',
        'sek': 'kr ',
        'nok': 'kr ',
        'dkk': 'kr ',
        'pln': 'zł ',
        'zar': 'R ',
        'inr': '₹',
        'brl': 'R$',
        'mxn': 'MX$',
        'jpy': '¥',
    }

    @staticmethod
    def get_stripe_amount(amount, currency):
        code = (currency or '').lower()
        if code in CheckoutIntegrationService.ZERO_DECIMAL_CURRENCIES:
            return int(round(amount))
        return int(round(amount * 100))

    @staticmethod
    def generate_agreement_access_token(session_id):
        secret = str(settings.SECRET_KEY).encode('utf-8')
        message = f'agreement:{session_id}'.encode('utf-8')
        return hmac.new(secret, message, hashlib.sha256).hexdigest()

    @staticmethod
    def verify_agreement_access_token(session_id, token):
        if not session_id or not token:
            return False
        expected = CheckoutIntegrationService.generate_agreement_access_token(session_id)
        return hmac.compare_digest(str(token).strip(), expected)

    @staticmethod
    def authorize_agreement_access(session_id, access_token=None, checkout_token=None):
        if checkout_token:
            return CheckoutIntegrationService.authenticate_session(session_id, checkout_token)
        if access_token and CheckoutIntegrationService.verify_agreement_access_token(session_id, access_token):
            session = ExternalCheckoutSession.all_objects.select_related(
                'tenant',
                'package_type',
                'package_type__location',
                'created_package',
                'customer_user',
            ).filter(id=session_id).first()
            if not session:
                raise NotFound('Checkout session not found.')
            return session
        raise PermissionDenied('Valid agreement access token or checkout session token is required.')

    @staticmethod
    def _render_welcome_template(text, tokens):
        rendered = str(text or '')
        for key, value in tokens.items():
            rendered = rendered.replace(key, str(value))
        return rendered

    @staticmethod
    def _format_currency_amount(amount, currency_code):
        code = (currency_code or 'usd').lower()
        symbol = CheckoutIntegrationService.CURRENCY_SYMBOLS.get(code, f'{code.upper()} ')
        if code in CheckoutIntegrationService.ZERO_DECIMAL_CURRENCIES:
            formatted = f'{Decimal(amount or 0):.0f}'
        else:
            formatted = f'{Decimal(amount or 0):.2f}'
        return symbol, f'{symbol}{formatted}'

    @staticmethod
    def get_signed_agreement_document(session):
        signature = HealthCommitmentSignature.all_objects.filter(
            tenant=session.tenant,
            checkout_session=session,
        ).first()
        if signature:
            return {
                'session_id': str(session.id),
                'tenant_name': session.tenant.name,
                'signer_name': signature.signer_name,
                'signer_email': signature.signer_email,
                'signed_at': signature.signed_at,
                'statement_version': signature.statement_version,
                'statement_title': signature.statement_title,
                'statement_body': signature.statement_body,
                'checkbox_label': signature.checkbox_label,
                'ip_address': signature.ip_address,
                'source': signature.source,
            }
        if session.health_commitment_accepted:
            signer_name = (
                session.health_commitment_signer_name
                or f'{session.first_name} {session.last_name}'.strip()
                or session.email
            )
            return {
                'session_id': str(session.id),
                'tenant_name': session.tenant.name,
                'signer_name': signer_name,
                'signer_email': session.email,
                'signed_at': session.health_commitment_accepted_at,
                'statement_version': session.health_commitment_version_snapshot or 1,
                'statement_title': session.health_commitment_title_snapshot or DEFAULT_HEALTH_COMMITMENT_TITLE,
                'statement_body': session.health_commitment_body_snapshot or DEFAULT_HEALTH_COMMITMENT_BODY,
                'checkbox_label': session.health_commitment_checkbox_label_snapshot or DEFAULT_HEALTH_COMMITMENT_CHECKBOX_LABEL,
                'ip_address': session.health_commitment_ip_address or session.client_ip_address,
                'source': 'website_checkout',
            }
        raise NotFound('No signed agreement was found for this checkout session.')

    @staticmethod
    def build_welcome_payload(session, base_url=None):
        config = TenantCheckoutConfig.objects.filter(tenant=session.tenant).first()
        tenant_name = session.tenant.name
        first_name = (
            session.first_name
            or (session.customer_user.first_name if session.customer_user else '')
            or 'there'
        ).strip()
        full_name = (
            f'{session.first_name} {session.last_name}'.strip()
            or (session.customer_user.get_full_name().strip() if session.customer_user else '')
            or first_name
        )
        package_name = (
            session.package_type.name if session.package_type else 'Membership Package'
        )
        credits = session.credit_count_snapshot or (
            session.package_type.credit_count if session.package_type else 0
        )
        class_word = 'class' if credits == 1 else 'classes'
        additional_benefits = (
            config.welcome_additional_benefits.strip()
            if config and config.welcome_additional_benefits
            else ''
        )
        whats_included = f'{credits} {class_word}'
        if additional_benefits:
            whats_included = f'{whats_included} — {additional_benefits}'

        currency_code = (session.currency or 'usd').lower()
        currency_symbol, amount_formatted = CheckoutIntegrationService._format_currency_amount(
            session.price_snapshot,
            currency_code,
        )

        billing_cycle = session.billing_cycle_snapshot or (
            session.package_type.billing_cycle if session.package_type else 'monthly'
        )
        is_recurring = bool(
            (session.created_package and session.created_package.stripe_subscription_id)
            or session.stripe_subscription_id
            or billing_cycle
        )
        if is_recurring and billing_cycle:
            payment_arrangement = f'Recurring {billing_cycle} payment of {amount_formatted}'
        else:
            payment_arrangement = f'One-off payment of {amount_formatted}'

        validity_days = session.validity_days_snapshot or (
            session.package_type.validity_days if session.package_type else 0
        )
        validity_period = (
            f'{validity_days} days validity'
            if validity_days
            else 'Standard package validity'
        )

        signature = HealthCommitmentSignature.all_objects.filter(
            tenant=session.tenant,
            checkout_session=session,
        ).first()
        agreement_available = bool(signature or session.health_commitment_accepted)
        agreement_view_url = None
        agreement_download_url = None
        if agreement_available:
            resolved_base = (base_url or getattr(settings, 'BASE_URL', '') or '').rstrip('/')
            access_token = CheckoutIntegrationService.generate_agreement_access_token(session.id)
            base_agreement_path = f'/api/v1/checkout/sessions/{session.id}/agreement/?access={access_token}'
            agreement_view_url = f'{resolved_base}{base_agreement_path}&format=html'
            agreement_download_url = f'{resolved_base}{base_agreement_path}&format=html&download=1'

        signer_name = (
            signature.signer_name if signature else (
                session.health_commitment_signer_name
                or f'{session.first_name} {session.last_name}'.strip()
                or session.email
            )
        )
        signed_at = signature.signed_at if signature else session.health_commitment_accepted_at
        statement_title = (
            signature.statement_title if signature
            else (session.health_commitment_title_snapshot or DEFAULT_HEALTH_COMMITMENT_TITLE)
        )
        statement_version = (
            signature.statement_version if signature
            else (session.health_commitment_version_snapshot or 1)
        )

        tokens = {
            '{tenant_name}': tenant_name,
            '{{tenant.name}}': tenant_name,
            '{first_name}': first_name,
            '{{contact.first_name}}': first_name,
            '{full_name}': full_name,
            '{package_name}': package_name,
            '{credits}': str(credits),
            '{amount}': amount_formatted,
            '{currency}': currency_code.upper(),
            '{payment_arrangement}': payment_arrangement,
            '{validity}': validity_period,
            '{agreement_url}': agreement_view_url or '',
        }

        raw_subject = config.welcome_email_subject if config else DEFAULT_WELCOME_EMAIL_SUBJECT
        raw_headline = config.welcome_headline if config else DEFAULT_WELCOME_HEADLINE
        raw_intro = config.welcome_intro_text if config else DEFAULT_WELCOME_INTRO_TEXT
        raw_agreement_note = (
            config.welcome_agreement_note if config else DEFAULT_WELCOME_AGREEMENT_NOTE
        )
        raw_body = config.welcome_body_text if config else DEFAULT_WELCOME_BODY_TEXT
        raw_closing = config.welcome_closing_text if config else DEFAULT_WELCOME_CLOSING_TEXT
        raw_tagline = config.welcome_tagline if config else ''
        reply_to_email = (
            config.welcome_reply_to_email.strip()
            if config and config.welcome_reply_to_email
            else ''
        )

        subject = CheckoutIntegrationService._render_welcome_template(raw_subject, tokens)
        headline = CheckoutIntegrationService._render_welcome_template(raw_headline, tokens)
        greeting = f'Hi {first_name},'
        intro = CheckoutIntegrationService._render_welcome_template(raw_intro, tokens)
        agreement_note = CheckoutIntegrationService._render_welcome_template(raw_agreement_note, tokens)
        custom_body = CheckoutIntegrationService._render_welcome_template(raw_body, tokens)
        closing = CheckoutIntegrationService._render_welcome_template(raw_closing, tokens)
        tagline = CheckoutIntegrationService._render_welcome_template(raw_tagline, tokens).strip()

        confirmation_items = [
            {'label': 'Package purchased', 'value': package_name},
            {'label': "What's included", 'value': whats_included},
            {'label': 'Amount paid', 'value': amount_formatted},
            {'label': 'Payment arrangement', 'value': payment_arrangement},
            {'label': 'Minimum term or package validity', 'value': validity_period},
        ]

        text_lines = [
            greeting,
            '',
            headline,
            intro,
            '',
            'Your purchase confirmation',
        ]
        for item in confirmation_items:
            text_lines.append(f'• {item["label"]}: {item["value"]}')

        if agreement_available:
            text_lines.extend([
                '',
                'Your agreement',
                'You can view and download your signed agreement here:',
                agreement_view_url,
            ])
            if agreement_note:
                text_lines.append(agreement_note)

        if custom_body:
            text_lines.extend(['', custom_body])

        if closing:
            text_lines.extend(['', closing])
        if tagline:
            text_lines.append(tagline)

        return {
            'tenant_name': tenant_name,
            'subject': subject,
            'headline': headline,
            'greeting': greeting,
            'intro': intro,
            'purchase_confirmation': {
                'title': 'Your purchase confirmation',
                'package_purchased': package_name,
                'whats_included': whats_included,
                'credits': credits,
                'additional_benefits': additional_benefits,
                'amount_paid': amount_formatted,
                'amount': str(session.price_snapshot),
                'currency': currency_code.upper(),
                'currency_symbol': currency_symbol,
                'payment_arrangement': payment_arrangement,
                'validity_period': validity_period,
                'items': confirmation_items,
            },
            'agreement': {
                'title': 'Your agreement',
                'signed': agreement_available,
                'signed_at': signed_at,
                'signer_name': signer_name if agreement_available else None,
                'statement_title': statement_title if agreement_available else None,
                'statement_version': statement_version if agreement_available else None,
                'description': 'You can view and download your signed agreement here:' if agreement_available else None,
                'view_url': agreement_view_url,
                'download_url': agreement_download_url,
                'note': agreement_note if agreement_available else '',
            },
            'custom_body': custom_body,
            'closing': closing,
            'tagline': tagline,
            'reply_to_email': reply_to_email,
            'full_text': '\n'.join(text_lines),
        }
