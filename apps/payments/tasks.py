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
