from celery import shared_task
from django.utils import timezone
from datetime import datetime
from apps.core.tenants.models import Tenant, Plan, TenantSubscription
from apps.core.tenants.services import TenantAdministrationService
import logging

logger = logging.getLogger(__name__)

@shared_task
def process_stripe_event(event_type, data):
    """
    Handles Stripe events asynchronously.
    Data is the 'data.object' dictionary from the event.
    """
    logger.info(f"Processing Stripe Event: {event_type}")

    try:
        if event_type == 'checkout.session.completed':
            _handle_checkout_completed(data)
        elif event_type == 'customer.subscription.updated':
            _handle_subscription_updated(data)
        elif event_type == 'customer.subscription.deleted':
            _handle_subscription_deleted(data)
        elif event_type == 'invoice.payment_failed':
            _handle_payment_failed(data)
            
    except Exception as e:
        # Celery will log the stack trace. 
        # We re-raise to trigger Celery retry mechanisms if configured.
        logger.error(f"Failed to process Stripe event: {e}")
        raise e

def _handle_checkout_completed(session):
    """
    Occurs when a user successfully pays for a subscription.
    """
    # 1. Extract Metadata (We put this here in Phase 2)
    metadata = session.get('metadata', {})
    tenant_id = metadata.get('tenant_id')
    plan_id = metadata.get('plan_id')
    
    if not tenant_id or not plan_id:
        logger.error("Missing metadata in checkout session")
        return

    # 2. Get Data
    stripe_subscription_id = session.get('subscription')
    stripe_customer_id = session.get('customer')

    # 3. Update DB
    tenant = Tenant.objects.get(id=tenant_id)
    plan = Plan.objects.get(id=plan_id)
    
    # Ensure customer ID is synced
    if not tenant.stripe_customer_id:
        tenant.stripe_customer_id = stripe_customer_id
        tenant.save()

    # 4. Create Subscription via Service (Handles old sub cancellation)
    # We use the internal service but explicitly set the Stripe ID
    subscription = TenantAdministrationService.assign_plan(tenant, plan)
    
    # Update the newly created subscription with Stripe details
    subscription.stripe_subscription_id = stripe_subscription_id
    subscription.save()

    # Also update any pending referral rewards to paid
    from apps.core.tenants.models import ReferralReward
    ReferralReward.objects.filter(subscription=subscription, status='pending').update(status='paid')
    
    logger.info(f"Provisioned Plan {plan.name} for Tenant {tenant.name}")

def _handle_subscription_updated(subscription_data):
    """
    Occurs when a plan renews, changes, or status updates.
    """
    stripe_id = subscription_data.get('id')
    status = subscription_data.get('status') # active, past_due, unpaid, canceled
    
    try:
        sub = TenantSubscription.objects.get(stripe_subscription_id=stripe_id)
        
        # Map Stripe status to Django status
        if status == 'active':
            sub.status = 'active'
            # Also update referral rewards to paid
            from apps.core.tenants.models import ReferralReward
            ReferralReward.objects.filter(subscription=sub, status='pending').update(status='paid')
        elif status in ['past_due', 'unpaid']:
            sub.status = 'past_due'
        elif status == 'canceled':
            sub.status = 'canceled'
        
        # Update Period End
        current_period_end = subscription_data.get('current_period_end')
        if current_period_end:
            sub.ends_at = datetime.fromtimestamp(current_period_end, tz=timezone.utc)
            
        sub.save()
        logger.info(f"Updated subscription {stripe_id} to {status}")
        
    except TenantSubscription.DoesNotExist:
        logger.warning(f"Subscription {stripe_id} not found in DB.")

def _handle_subscription_deleted(subscription_data):
    """
    Occurs when a subscription is explicitly canceled.
    """
    stripe_id = subscription_data.get('id')
    try:
        sub = TenantSubscription.objects.get(stripe_subscription_id=stripe_id)
        sub.status = 'canceled'
        sub.ends_at = timezone.now()
        sub.save()
        logger.info(f"Canceled subscription {stripe_id}")
    except TenantSubscription.DoesNotExist:
        pass

def _handle_payment_failed(invoice):
    """
    Occurs when a payment fails.
    """
    stripe_subscription_id = invoice.get('subscription')
    if stripe_subscription_id:
        try:
            sub = TenantSubscription.objects.get(stripe_subscription_id=stripe_subscription_id)
            sub.status = 'past_due'
            sub.save()
        except TenantSubscription.DoesNotExist:
            pass


@shared_task
def check_expired_trials():
    """
    Checks for subscriptions whose trials have expired and marks them as past_due.
    """
    now = timezone.now()
    expired_subscriptions = TenantSubscription.objects.filter(
        status='active',
        trial_ends_at__lt=now
    )
    
    count = 0
    for sub in expired_subscriptions:
        # If they haven't set up Stripe yet (or subscription ID isn't synced), the trial has expired
        if not sub.stripe_subscription_id:
            sub.status = 'past_due'
            sub.save()
            count += 1
            logger.info(f"Subscription for tenant {sub.tenant.name} has expired from trial. Status set to past_due.")
            
    return f"Checked expired trials. Updated {count} subscriptions."
        