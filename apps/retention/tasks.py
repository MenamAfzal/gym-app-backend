import logging
from celery import shared_task
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import bypass_tenant_isolation
from .services import RetentionMetricsService

logger = logging.getLogger(__name__)


@shared_task(name='retention.recalculate_all_tenants_metrics')
def recalculate_all_tenants_metrics():
    """
    Scheduled nightly job that iterates all active gym tenants and
    recalculates comprehensive retention metrics and churn risk scores.
    """
    logger.info("Starting retention metrics recalculation for all active tenants...")
    with bypass_tenant_isolation():
        active_tenants = Tenant.objects.filter(is_active=True)
        count = 0
        for tenant in active_tenants:
            try:
                processed = RetentionMetricsService.recalculate_for_tenant(tenant.id)
                count += processed
                logger.info(f"Processed {processed} clients for tenant {tenant.name} ({tenant.id})")
                try:
                    RetentionMetricsService.capture_daily_snapshot(tenant)
                except Exception as snap_err:
                    logger.exception(f"Failed to capture daily snapshot for tenant {tenant.id}: {snap_err}")
            except Exception as e:
                logger.exception(f"Failed to recalculate retention metrics for tenant {tenant.id}: {str(e)}")
        
        logger.info(f"Completed recalculate_all_tenants_metrics across {active_tenants.count()} tenants ({count} total clients).")
        return count


@shared_task(name='retention.capture_all_tenants_daily_snapshots')
def capture_all_tenants_daily_snapshots():
    """
    Captures daily retention snapshots for all active tenants.
    """
    logger.info("Starting daily snapshot capture for all active tenants...")
    with bypass_tenant_isolation():
        active_tenants = Tenant.objects.filter(is_active=True)
        count = 0
        for tenant in active_tenants:
            try:
                RetentionMetricsService.capture_daily_snapshot(tenant)
                count += 1
            except Exception as e:
                logger.exception(f"Failed to capture daily snapshot for tenant {tenant.id}: {str(e)}")
        logger.info(f"Captured daily snapshots for {count} tenants.")
        return count


@shared_task(name='retention.recalculate_single_client_metrics')
def recalculate_single_client_metrics(tenant_id, client_id):
    """
    Event-driven task triggered when an individual client action occurs:
    - Booking checked in
    - Booking cancelled / late cancelled
    - Session marked no-show
    - Package purchased or renewed
    - Facility physical check-in
    """
    logger.info(f"Triggering single client retention metric recalculation for client {client_id} in tenant {tenant_id}")
    try:
        return RetentionMetricsService.recalculate_for_tenant(
            tenant_id=tenant_id,
            client_ids=[client_id]
        )
    except Exception as e:
        logger.exception(f"Error recalculating metrics for client {client_id} in tenant {tenant_id}: {str(e)}")
        return 0
