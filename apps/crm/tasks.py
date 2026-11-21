import logging
from celery import shared_task
from django.utils import timezone
from apps.crm.client import CRMClientError
from apps.crm.models import CRMOutboxEvent
from apps.crm.services import CRMIntegrationService

logger = logging.getLogger(__name__)

MAX_EVENT_ATTEMPTS = 5


@shared_task(bind=True, max_retries=MAX_EVENT_ATTEMPTS, name="crm.deliver_crm_event_task")
def deliver_crm_event_task(self, outbox_event_id: str):
    """
    Delivers a pending CRM outbox event with automatic retries and exponential backoff.
    """
    try:
        outbox_event = CRMOutboxEvent.all_objects.select_related("tenant").get(id=outbox_event_id)
    except CRMOutboxEvent.DoesNotExist:
        logger.warning("CRMOutboxEvent %s not found for delivery", outbox_event_id)
        return {"status": "not_found"}

    if outbox_event.status == CRMOutboxEvent.Status.DELIVERED:
        return {"status": "already_delivered", "event_id": outbox_event.event_id}

    client = CRMIntegrationService.get_client_for_tenant(outbox_event.tenant)
    if not client:
        outbox_event.status = CRMOutboxEvent.Status.FAILED
        outbox_event.error_message = "CRM credentials not configured for tenant"
        outbox_event.last_attempt_at = timezone.now()
        outbox_event.save(update_fields=["status", "error_message", "last_attempt_at"])
        return {"status": "not_configured"}

    outbox_event.attempts += 1
    outbox_event.last_attempt_at = timezone.now()

    try:
        status_code, response_data = client.ingest_event(outbox_event.payload)
        outbox_event.response_status_code = status_code
        outbox_event.response_body = response_data

        if status_code in (200, 202):
            outbox_event.status = CRMOutboxEvent.Status.DELIVERED
            outbox_event.error_message = ""
            outbox_event.save(update_fields=[
                "status", "attempts", "last_attempt_at",
                "response_status_code", "response_body", "error_message"
            ])
            logger.info("CRM event %s delivered successfully with status %s", outbox_event.event_id, status_code)
            return {"status": "delivered", "status_code": status_code, "event_id": outbox_event.event_id}

        # Handle non-retryable 4xx client errors (bad auth, unsupported type, malformed payload)
        if 400 <= status_code < 500:
            outbox_event.status = CRMOutboxEvent.Status.FAILED
            outbox_event.error_message = str(response_data.get("detail") or response_data)
            outbox_event.save(update_fields=[
                "status", "attempts", "last_attempt_at",
                "response_status_code", "response_body", "error_message"
            ])
            logger.error("CRM rejected event %s with client error %s: %s", outbox_event.event_id, status_code, response_data)
            return {"status": "client_error", "status_code": status_code}

        # 5xx server error - retryable
        err_msg = f"CRM server error HTTP {status_code}: {response_data}"
        outbox_event.error_message = err_msg

    except CRMClientError as exc:
        err_msg = str(exc)
        outbox_event.error_message = err_msg
        outbox_event.response_status_code = getattr(exc, "status_code", None)

    # Determine retry or abandonment
    if outbox_event.attempts >= MAX_EVENT_ATTEMPTS:
        outbox_event.status = CRMOutboxEvent.Status.ABANDONED
        outbox_event.save(update_fields=[
            "status", "attempts", "last_attempt_at",
            "response_status_code", "response_body", "error_message"
        ])
        logger.error("CRM event %s abandoned after %s attempts: %s", outbox_event.event_id, outbox_event.attempts, outbox_event.error_message)
        return {"status": "abandoned", "attempts": outbox_event.attempts}

    outbox_event.save(update_fields=[
        "status", "attempts", "last_attempt_at",
        "response_status_code", "response_body", "error_message"
    ])

    countdown = min(300, (2 ** outbox_event.attempts) * 10)
    logger.warning("Retrying CRM event %s in %s seconds (attempt %s/%s)", outbox_event.event_id, countdown, outbox_event.attempts, MAX_EVENT_ATTEMPTS)
    raise self.retry(countdown=countdown, exc=Exception(outbox_event.error_message))


@shared_task(name="crm.sync_tenant_packages_to_crm_task")
def sync_tenant_packages_to_crm_task(tenant_id: str):
    """
    Background task to bulk sync all packages for a tenant to CRM.
    """
    from apps.core.tenants.models import Tenant
    tenant = Tenant.objects.filter(id=tenant_id).first()
    if not tenant:
        return {"status": "tenant_not_found"}

    success, resp = CRMIntegrationService.sync_all_packages_to_crm(tenant)
    return {"status": "success" if success else "failed", "response": resp}


@shared_task(name="crm.retry_pending_crm_events_task")
def retry_pending_crm_events_task():
    """
    Periodic maintenance task to sweep stranded pending or failed CRM outbox events.
    """
    due_events = list(
        CRMOutboxEvent.all_objects.filter(
            status__in=[CRMOutboxEvent.Status.PENDING, CRMOutboxEvent.Status.FAILED],
            attempts__lt=MAX_EVENT_ATTEMPTS,
        ).order_by("created_at")[:100].values_list("id", flat=True)
    )

    dispatched = 0
    for event_id in due_events:
        deliver_crm_event_task.delay(str(event_id))
        dispatched += 1

    return {"dispatched": dispatched}


@shared_task(name="crm.reconcile_all_tenant_packages_to_crm_task")
def reconcile_all_tenant_packages_to_crm_task():
    """
    Periodic reconciliation task sweeping active CRM-enabled tenants to ensure package catalog consistency.
    """
    from apps.crm.models import TenantCRMConfig
    active_configs = TenantCRMConfig.all_objects.filter(is_active=True).select_related("tenant")
    results = {}
    for cfg in active_configs:
        success, resp = CRMIntegrationService.sync_all_packages_to_crm(cfg.tenant)
        results[str(cfg.tenant_id)] = {"success": success, "synced": resp.get("total", 0)}
    return results


@shared_task(name="crm.reconcile_expired_packages_task")
def reconcile_expired_packages_task():
    """
    Periodic task scanning for packages whose validity period has elapsed.
    Marks them expired and dispatches subscription.expired events to CRM.
    """
    from apps.scheduling.models import Package
    now = timezone.now()
    expired_packages = list(
        Package.all_objects.select_related("package_type", "client", "tenant")
        .filter(status="active", expires_at__lte=now)[:100]
    )

    reconciled = 0
    for pkg in expired_packages:
        pkg.status = "expired"
        pkg.save(update_fields=["status"])
        try:
            CRMIntegrationService.dispatch_subscription_expired_event(pkg, reason="validity_period_ended")
            reconciled += 1
        except Exception as exc:
            logger.warning("Failed to dispatch subscription.expired for Package %s: %s", pkg.id, exc)

    return {"reconciled_expired_count": reconciled}
