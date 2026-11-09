import logging
from celery import shared_task
from apps.core.tenants.models import Tenant
from .services import LedgerService

logger = logging.getLogger(__name__)

@shared_task
def process_scheduled_payouts():
    """
    Background job to process payouts for all active tenants.
    Runs periodically (e.g., weekly) as configured in Celery Beat.
    """
    logger.info("Starting scheduled payout processing...")
    
    # Ideally, only fetch tenants that have active statuses or aren't suspended.
    # For now, we process all tenants.
    tenants = Tenant.objects.all()
    
    payouts_created = 0
    
    for tenant in tenants:
        try:
            # Bypass isolation isn't strictly needed here because the service 
            # uses explicit filters on tenant, but we handle the iteration carefully.
            payout = LedgerService.process_payout_for_tenant(tenant)
            
            if payout:
                logger.info(f"Created payout {payout.id} for tenant {tenant.name} (Amount: {payout.amount})")
                payouts_created += 1
                
                # TODO: Dispatch an event or trigger the actual Stripe Transfer API here.
                # Example:
                # stripe.Transfer.create(
                #   amount=int(payout.amount * 100),
                #   currency=payout.currency,
                #   destination=tenant.stripe_account_id,
                # )
                
        except Exception as e:
            logger.error(f"Failed to process payout for tenant {tenant.name}: {str(e)}")
            # Continue processing other tenants even if one fails
            continue
            
    logger.info(f"Finished scheduled payout processing. Created {payouts_created} payouts.")
    return payouts_created


@shared_task
def send_meta_capi_event_task(session_id, event_name):
    from apps.payments.meta_capi_service import MetaConversionsService
    MetaConversionsService.send_event(session_id=session_id, event_name=event_name)


@shared_task
def send_checkout_welcome_email_task(session_id):
    from datetime import timedelta
    from django.conf import settings
    from django.core.mail import EmailMultiAlternatives
    from django.utils import timezone
    from django.utils.html import strip_tags
    from apps.payments.models import ExternalCheckoutSession
    from apps.users.models import EmailOTP, OTPPurpose

    session = ExternalCheckoutSession.all_objects.select_related('tenant', 'package_type').filter(id=session_id).first()
    if not session:
        return

    tenant = session.tenant
    package = session.package_type
    email = session.email

    code = ''
    if session.is_new_customer:
        code = ''.join(str(session.initiate_event_id.int % 1000000).zfill(6))
        otp_hash = EmailOTP.hash_code(code)
        EmailOTP.objects.create(
            email=email,
            otp_hash=otp_hash,
            purpose=OTPPurpose.CHECKOUT,
            expires_at=timezone.now() + timedelta(days=7)
        )

    app_deep_link = f'fitverx://login?tenant_id={tenant.id}'
    subject = f'Welcome to {tenant.name} – Membership Confirmed!'
    body_text = f"""Hello,

Your membership for {package.name} at {tenant.name} has been successfully activated.

Download the Fitverx mobile app to book classes and access workouts:
{app_deep_link}
"""
    if session.is_new_customer:
        body_text += f"\nYour one-time password setup verification code is: {code}\n"

    msg = EmailMultiAlternatives(
        subject=subject,
        body=body_text,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email]
    )
    msg.send(fail_silently=True)


@shared_task
def expire_abandoned_sessions_task():
    from django.utils import timezone
    from apps.payments.models import ExternalCheckoutSession
    now = timezone.now()
    return ExternalCheckoutSession.all_objects.filter(
        status__in=[
            ExternalCheckoutSession.StatusChoices.OPEN,
            ExternalCheckoutSession.StatusChoices.PAYMENT_PENDING
        ],
        expires_at__lt=now
    ).update(status=ExternalCheckoutSession.StatusChoices.EXPIRED)


@shared_task
def purge_old_checkout_tracking_data_task():
    from datetime import timedelta
    from django.utils import timezone
    from apps.payments.models import ExternalCheckoutSession
    threshold = timezone.now() - timedelta(days=30)
    return ExternalCheckoutSession.all_objects.filter(
        created_at__lt=threshold,
        tracking_purged_at__isnull=True
    ).update(
        client_ip_address=None,
        client_user_agent=None,
        fbp=None,
        fbc=None,
        tracking_purged_at=timezone.now()
    )

