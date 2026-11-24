import logging
from celery import shared_task
from django.utils import timezone

from apps.ghl.client import GHLApiError, GHLAuthError, GHLRateLimitError
from apps.ghl.models import GHLOutboxEvent, TenantGHLConnection
from apps.ghl.oauth import refresh_connection_tokens
from apps.ghl.services import GHLIntegrationService

logger = logging.getLogger(__name__)

MAX_GHL_EVENT_ATTEMPTS = 5


@shared_task(bind=True, max_retries=MAX_GHL_EVENT_ATTEMPTS, name="ghl.deliver_ghl_outbox_event_task")
def deliver_ghl_outbox_event_task(self, outbox_event_id: str):
    """
    Delivers a pending GoHighLevel outbox event with automatic retries and exponential backoff.
    """
    try:
        outbox_event = GHLOutboxEvent.all_objects.select_related("tenant").get(id=outbox_event_id)
    except GHLOutboxEvent.DoesNotExist:
        logger.warning("GHLOutboxEvent %s not found for delivery", outbox_event_id)
        return {"status": "not_found"}

    if outbox_event.status == GHLOutboxEvent.Status.DELIVERED:
        return {"status": "already_delivered", "event_id": outbox_event.event_id}

    connection = GHLIntegrationService.get_connection_for_tenant(outbox_event.tenant)
    if not connection or not connection.is_connected:
        outbox_event.status = GHLOutboxEvent.Status.FAILED
        outbox_event.error_message = "GoHighLevel connection not active for tenant"
        outbox_event.last_attempt_at = timezone.now()
        outbox_event.save(update_fields=["status", "error_message", "last_attempt_at"])
        return {"status": "not_connected"}

    outbox_event.attempts += 1
    outbox_event.last_attempt_at = timezone.now()

    try:
        result = GHLIntegrationService.sync_purchase_to_ghl(outbox_event)
        outbox_event.status = GHLOutboxEvent.Status.DELIVERED
        outbox_event.response_status_code = 200
        outbox_event.response_body = result
        outbox_event.error_message = ""
        outbox_event.save(update_fields=[
            "status", "attempts", "last_attempt_at",
            "response_status_code", "response_body", "error_message"
        ])
        logger.info("GHL event %s successfully synced: %s", outbox_event.event_id, result.get("action"))
        return {"status": "delivered", "result": result}

    except GHLAuthError as auth_exc:
        logger.warning("GHL auth error on event %s, attempting token refresh...", outbox_event.event_id)
        try:
            refresh_connection_tokens(connection)
            # Retry immediately with refreshed token
            result = GHLIntegrationService.sync_purchase_to_ghl(outbox_event)
            outbox_event.status = GHLOutboxEvent.Status.DELIVERED
            outbox_event.response_status_code = 200
            outbox_event.response_body = result
            outbox_event.error_message = ""
            outbox_event.save(update_fields=[
                "status", "attempts", "last_attempt_at",
                "response_status_code", "response_body", "error_message"
            ])
            return {"status": "delivered", "result": result}
        except Exception as refresh_err:
            outbox_event.error_message = f"Auth error and refresh failed: {refresh_err}"
            outbox_event.response_status_code = 401

    except GHLRateLimitError as rate_exc:
        outbox_event.response_status_code = 429
        outbox_event.error_message = str(rate_exc)
        if outbox_event.attempts < MAX_GHL_EVENT_ATTEMPTS:
            outbox_event.save(update_fields=["attempts", "last_attempt_at", "response_status_code", "error_message"])
            countdown = getattr(rate_exc, 'retry_after', 60)
            logger.warning("GHL rate limited on event %s, retrying in %s seconds", outbox_event.event_id, countdown)
            raise self.retry(countdown=countdown, exc=rate_exc)

    except GHLApiError as api_exc:
        status_code = getattr(api_exc, 'status_code', None)
        outbox_event.response_status_code = status_code
        outbox_event.error_message = str(api_exc)
        # Non-retryable 4xx errors
        if status_code and 400 <= status_code < 500 and status_code not in (401, 429):
            outbox_event.status = GHLOutboxEvent.Status.FAILED
            outbox_event.save(update_fields=[
                "status", "attempts", "last_attempt_at",
                "response_status_code", "error_message"
            ])
            logger.error("GHL client error on event %s: %s", outbox_event.event_id, api_exc)
            return {"status": "failed", "error": str(api_exc)}

    except Exception as generic_exc:
        outbox_event.error_message = str(generic_exc)

    # Retry or abandon
    if outbox_event.attempts >= MAX_GHL_EVENT_ATTEMPTS:
        outbox_event.status = GHLOutboxEvent.Status.ABANDONED
        outbox_event.save(update_fields=[
            "status", "attempts", "last_attempt_at",
            "response_status_code", "error_message"
        ])
        logger.error("GHL event %s abandoned after %s attempts: %s", outbox_event.event_id, outbox_event.attempts, outbox_event.error_message)
        return {"status": "abandoned", "error": outbox_event.error_message}

    outbox_event.save(update_fields=[
        "status", "attempts", "last_attempt_at",
        "response_status_code", "error_message"
    ])
    countdown = min(300, (2 ** outbox_event.attempts) * 10)
    logger.warning("Retrying GHL event %s in %s seconds (attempt %s/%s)", outbox_event.event_id, countdown, outbox_event.attempts, MAX_GHL_EVENT_ATTEMPTS)
    raise self.retry(countdown=countdown, exc=Exception(outbox_event.error_message))
