"""
Notification Celery Tasks

Background processing pipeline:

    dispatch_campaign_task      → resolves audience, fans out deliver_inbox_task per user
    deliver_inbox_task          → dispatches one inbox item to all channels
    process_scheduled_campaigns → beat task (every 1 min) — finds due campaigns
    process_time_based_automations → beat task (every 5 min) — class reminders, expiry

Key guarantees:
    - Idempotent: retries do not create duplicate notifications
    - Tenant-isolated: cross-tenant context set/reset around all DB operations
    - Chunked: audiences processed 100 at a time (never loads entire tenant into memory)
    - Transaction-safe: inbox items created inside atomic blocks;
      deliver_inbox_task enqueued via transaction.on_commit
    - Cancellation-safe: dispatch_campaign_task checks for CANCELLED status before processing
"""
import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

logger = logging.getLogger(__name__)

CHUNK_SIZE = 100  # Process audience in chunks of this size


def _chunked_queryset(qs, chunk_size=CHUNK_SIZE):
    """Iterate a queryset in chunks to avoid loading entire result into memory."""
    offset = 0
    while True:
        chunk = list(qs[offset:offset + chunk_size])
        if not chunk:
            break
        yield chunk
        offset += chunk_size


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='notifications.dispatch_campaign')
def dispatch_campaign_task(self, campaign_id: str):
    """
    Resolve audience and create per-user NotificationInbox items.
    Fans out deliver_inbox_task for each inbox item.

    Idempotency: checks idempotency_key before creating each inbox item.
    Cancellation: checks status != CANCELLED before processing.
    Chunking: processes audience in chunks of 100.
    """
    from apps.notifications.models import (
        NotificationCampaign, NotificationStatus, NotificationInbox,
    )
    from apps.notifications.audience import AudienceResolver
    from apps.core.tenants.context import set_current_tenant, reset_current_tenant

    try:
        campaign = NotificationCampaign.all_objects.select_related(
            'tenant', 'template', 'audience_group', 'recurrence_rule'
        ).get(id=campaign_id)
    except NotificationCampaign.DoesNotExist:
        logger.error(f"dispatch_campaign_task: Campaign {campaign_id} not found.")
        return

    # Set tenant context for this task
    token = set_current_tenant(campaign.tenant)
    try:
        # Guard: check CANCELLED status before processing (race condition with cancel_campaign)
        if campaign.status == NotificationStatus.CANCELLED:
            logger.info(f"Campaign {campaign_id} is CANCELLED — skipping dispatch.")
            return

        # Mark as PROCESSING
        with transaction.atomic():
            campaign_locked = NotificationCampaign.all_objects.select_for_update().get(id=campaign_id)
            if campaign_locked.status == NotificationStatus.CANCELLED:
                logger.info(f"Campaign {campaign_id} was cancelled while acquiring lock — skipping.")
                return
            campaign_locked.status = NotificationStatus.PROCESSING
            campaign_locked.save(update_fields=['status'])

        # Resolve audience
        users_qs = AudienceResolver.resolve(campaign)
        recipient_count = 0

        # Process in chunks
        for user_batch in _chunked_queryset(users_qs.only('id', 'email', 'tenant_id'), CHUNK_SIZE):
            for user in user_batch:
                idem_key = f"campaign:{campaign_id}:user:{user.id}"

                # Idempotency check
                if NotificationInbox.all_objects.filter(idempotency_key=idem_key).exists():
                    continue

                with transaction.atomic():
                    inbox_item = NotificationInbox.objects.create(
                        campaign=campaign,
                        recipient=user,
                        tenant=campaign.tenant,
                        title=campaign.title,
                        body=campaign.body,
                        notification_type=campaign.notification_type,
                        priority=campaign.priority,
                        source=campaign.source,
                        delivery_policy=campaign.delivery_policy,
                        action_payload=campaign.action_payload,
                        idempotency_key=idem_key,
                    )
                    recipient_count += 1

                    # Enqueue delivery AFTER successful DB commit
                    transaction.on_commit(
                        lambda iid=str(inbox_item.id): deliver_inbox_task.delay(iid)
                    )

        # Update campaign status and counters
        with transaction.atomic():
            final_campaign = NotificationCampaign.all_objects.select_for_update().get(id=campaign_id)

            # Don't overwrite CANCELLED
            if final_campaign.status != NotificationStatus.CANCELLED:
                final_campaign.recipient_count = recipient_count
                final_campaign.processed_at    = timezone.now()

                # Determine final status
                if recipient_count == 0:
                    final_campaign.status = NotificationStatus.SENT  # No recipients = trivially sent
                else:
                    final_campaign.status = NotificationStatus.SENT  # Will be updated to PARTIALLY_SENT by monitors

                # Handle recurring: calculate next_run_at
                if final_campaign.recurrence_rule and final_campaign.recurrence_rule.is_active:
                    next_run = _calculate_next_run(
                        final_campaign.recurrence_rule,
                        final_campaign.tenant,
                    )
                    if next_run:
                        final_campaign.next_run_at = next_run
                        final_campaign.status      = NotificationStatus.SCHEDULED
                    else:
                        final_campaign.recurrence_rule.is_active = False
                        final_campaign.recurrence_rule.save(update_fields=['is_active'])
                else:
                    final_campaign.next_run_at = None

                final_campaign.save(update_fields=[
                    'status', 'recipient_count', 'processed_at', 'next_run_at'
                ])

        logger.info(f"Campaign {campaign_id}: dispatched to {recipient_count} recipients.")

    except Exception as exc:
        logger.error(f"dispatch_campaign_task failed for campaign {campaign_id}: {exc}")
        try:
            NotificationCampaign.all_objects.filter(id=campaign_id).update(
                status=NotificationStatus.FAILED
            )
        except Exception:
            pass
        raise self.retry(exc=exc)
    finally:
        reset_current_tenant(token)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, name='notifications.deliver_inbox')
def deliver_inbox_task(self, inbox_item_id: str):
    """
    Deliver a single NotificationInbox item to all configured channels.

    Idempotent: checks push_sent/email_sent before re-sending on retry.
    Email failures do not affect push delivery state (or vice versa).
    """
    from apps.notifications.models import NotificationInbox, DeliveryPolicy
    from apps.notifications.providers import NotificationDispatcher

    try:
        inbox_item = NotificationInbox.all_objects.select_related(
            'recipient', 'tenant', 'campaign'
        ).get(id=inbox_item_id)
    except NotificationInbox.DoesNotExist:
        logger.warning(f"deliver_inbox_task: NotificationInbox {inbox_item_id} not found.")
        return

    # Idempotency on retry
    policy = inbox_item.delivery_policy
    if policy == DeliveryPolicy.PUSH_ONLY and inbox_item.push_sent:
        logger.debug(f"Inbox {inbox_item_id}: push already sent — skipping retry.")
        return
    if policy == DeliveryPolicy.PUSH_AND_EMAIL and inbox_item.push_sent and inbox_item.email_sent:
        logger.debug(f"Inbox {inbox_item_id}: fully delivered — skipping retry.")
        return

    try:
        NotificationDispatcher.dispatch(inbox_item)
    except Exception as exc:
        logger.error(f"deliver_inbox_task failed for inbox {inbox_item_id}: {exc}")
        # Exponential backoff: 30s, 60s, 120s
        countdown = 30 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)


@shared_task(name='notifications.process_scheduled_campaigns')
def process_scheduled_campaigns_task():
    """
    Beat task — runs every 1 minute.

    Finds NotificationCampaigns with status=SCHEDULED and next_run_at <= now.
    Enqueues dispatch_campaign_task for each.
    """
    from apps.notifications.models import NotificationCampaign, NotificationStatus
    from apps.core.tenants.context import set_current_tenant, reset_current_tenant

    now = timezone.now()

    due_campaigns = NotificationCampaign.all_objects.filter(
        status=NotificationStatus.SCHEDULED,
        next_run_at__lte=now,
    ).select_related('tenant', 'recurrence_rule')

    count = 0
    for campaign in due_campaigns:
        token = set_current_tenant(campaign.tenant)
        try:
            dispatch_campaign_task.delay(str(campaign.id))
            count += 1
        except Exception as e:
            logger.error(f"process_scheduled_campaigns_task: Failed to enqueue campaign {campaign.id}: {e}")
        finally:
            reset_current_tenant(token)

    if count:
        logger.info(f"process_scheduled_campaigns_task: Enqueued {count} campaigns.")


@shared_task(name='notifications.process_time_based_automations')
def process_time_based_automations_task():
    """
    Beat task — runs every 5 minutes.

    Handles time-based automated notifications:
    - Class reminders (24h and 1h before session start)
    - Appointment reminders (24h and 1h before appointment)
    - Membership expiry warnings (7 days, 3 days, 1 day before expiry)
    """
    from apps.notifications.services import NotificationService
    from apps.notifications.events import (
        ClassReminder24hEvent, ClassReminder1hEvent,
        AppointmentReminderEvent, MembershipExpiringEvent,
    )
    from apps.scheduling.models import ClassSession, Booking, Appointment, Package
    from apps.core.tenants.context import set_current_tenant, reset_current_tenant

    now = timezone.now()

    target_24h_start = now + timedelta(hours=23, minutes=45)
    target_24h_end   = now + timedelta(hours=24, minutes=15)

    sessions_24h = ClassSession.all_objects.filter(
        start_at__gte=target_24h_start,
        start_at__lte=target_24h_end,
        status='scheduled',
    ).select_related('template', 'tenant')

    for session in sessions_24h:
        token = set_current_tenant(session.tenant)
        try:
            bookings = Booking.all_objects.filter(
                session=session, status='booked'
            ).select_related('client', 'client__profile', 'tenant')
            for booking in bookings:
                try:
                    NotificationService.handle_event(ClassReminder24hEvent(
                        tenant_id=booking.tenant_id,
                        recipient_id=booking.client_id,
                        entity_id=booking.id,
                        context_data={
                            'client_name': getattr(booking.client, 'profile', None) and
                                           booking.client.profile.first_name or booking.client.email,
                            'class_name': session.template.name,
                            'class_time': str(session.start_at),
                            'gym_name': booking.tenant.name,
                        },
                    ))
                except Exception as e:
                    logger.error(f"24h reminder failed for booking {booking.id}: {e}")
        finally:
            reset_current_tenant(token)

    target_1h_start = now + timedelta(minutes=45)
    target_1h_end   = now + timedelta(minutes=75)

    sessions_1h = ClassSession.all_objects.filter(
        start_at__gte=target_1h_start,
        start_at__lte=target_1h_end,
        status='scheduled',
    ).select_related('template', 'tenant')

    for session in sessions_1h:
        token = set_current_tenant(session.tenant)
        try:
            bookings = Booking.all_objects.filter(
                session=session, status='booked'
            ).select_related('client', 'client__profile', 'tenant')
            for booking in bookings:
                try:
                    NotificationService.handle_event(ClassReminder1hEvent(
                        tenant_id=booking.tenant_id,
                        recipient_id=booking.client_id,
                        entity_id=booking.id,
                        context_data={
                            'client_name': getattr(booking.client, 'profile', None) and
                                           booking.client.profile.first_name or booking.client.email,
                            'class_name': session.template.name,
                            'class_time': str(session.start_at),
                            'gym_name': booking.tenant.name,
                        },
                    ))
                except Exception as e:
                    logger.error(f"1h reminder failed for booking {booking.id}: {e}")
        finally:
            reset_current_tenant(token)

    target_appt_start = now + timedelta(hours=23, minutes=45)
    target_appt_end   = now + timedelta(hours=24, minutes=15)

    appointments = Appointment.all_objects.filter(
        start_at__gte=target_appt_start,
        start_at__lte=target_appt_end,
        status='scheduled',
    ).select_related('client', 'client__profile', 'provider', 'tenant')

    for appointment in appointments:
        token = set_current_tenant(appointment.tenant)
        try:
            try:
                NotificationService.handle_event(AppointmentReminderEvent(
                    tenant_id=appointment.tenant_id,
                    recipient_id=appointment.client_id,
                    entity_id=appointment.id,
                    context_data={
                        'client_name': getattr(appointment.client, 'profile', None) and
                                       appointment.client.profile.first_name or appointment.client.email,
                        'trainer_name': getattr(appointment.provider, 'profile', None) and
                                        appointment.provider.profile.first_name or appointment.provider.email,
                        'appointment_time': str(appointment.start_at),
                        'gym_name': appointment.tenant.name,
                    },
                ))
            except Exception as e:
                logger.error(f"Appointment reminder failed for appointment {appointment.id}: {e}")
        finally:
            reset_current_tenant(token)

    for days in [7, 3, 1]:
        expiry_start = now + timedelta(days=days) - timedelta(hours=1)
        expiry_end   = now + timedelta(days=days) + timedelta(hours=1)

        expiring_packages = Package.all_objects.filter(
            expires_at__gte=expiry_start,
            expires_at__lte=expiry_end,
            credits_remaining__gt=0,
        ).select_related('client', 'client__profile', 'package_type', 'tenant')

        for pkg in expiring_packages:
            token = set_current_tenant(pkg.tenant)
            try:
                try:
                    NotificationService.handle_event(MembershipExpiringEvent(
                        tenant_id=pkg.tenant_id,
                        recipient_id=pkg.client_id,
                        entity_id=pkg.id,
                        context_data={
                            'client_name': getattr(pkg.client, 'profile', None) and
                                           pkg.client.profile.first_name or pkg.client.email,
                            'package_name': pkg.package_type.name,
                            'expiry_date': str(pkg.expires_at.date()),
                            'days_remaining': str(days),
                            'gym_name': pkg.tenant.name,
                        },
                    ))
                except Exception as e:
                    logger.error(f"Membership expiry notification failed for package {pkg.id}: {e}")
            finally:
                reset_current_tenant(token)

    logger.info("process_time_based_automations_task: Completed.")


def _calculate_next_run(recurrence_rule, tenant):
    """
    Calculate the next run datetime for a recurring notification.
    Uses the same days_of_week/start_date/end_date/send_time schema as RecurrenceRule.

    Returns: timezone-aware datetime in UTC, or None if schedule is exhausted.
    """
    from apps.notifications.models import TenantNotificationSettings
    from datetime import datetime, date

    try:
        import pytz
        tn_settings = TenantNotificationSettings.get_or_create_for_tenant(tenant)
        tz = pytz.timezone(tn_settings.timezone)
    except Exception:
        import pytz
        tz = pytz.utc

    today = timezone.now().astimezone(tz).date()
    end_date = recurrence_rule.end_date

    if today > end_date:
        return None  # Schedule exhausted

    WEEKDAY_MAP = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6,
    }

    days_of_week = recurrence_rule.days_of_week
    if not isinstance(days_of_week, list):
        return None

    target_weekdays = {WEEKDAY_MAP.get(d.lower()) for d in days_of_week if d.lower() in WEEKDAY_MAP}
    if not target_weekdays:
        return None

    # Find next occurrence starting from tomorrow
    check_date = today + timedelta(days=1)
    for _ in range(8):  # Look at most 8 days ahead
        if check_date > end_date:
            return None
        if check_date.weekday() in target_weekdays:
            # Convert to UTC
            import datetime as dt
            naive_dt = dt.datetime.combine(check_date, recurrence_rule.send_time)
            local_dt = tz.localize(naive_dt)
            utc_dt   = local_dt.astimezone(pytz.utc)
            return utc_dt
        check_date += timedelta(days=1)

    return None
