from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from django.db import transaction
import logging

from .models import (
    ClassSession, Booking, Waitlist, SubstituteRequest, Package,
    Notification, Location, StaffLocation
)
from apps.users.models import User, UserRole

logger = logging.getLogger(__name__)

@shared_task
def run_reminder_job():
    """
    Scheduled task (runs e.g. every 5-15 mins).
    Scans upcoming sessions and sends reminders at 24h and 1h offsets.
    """
    logger.info("Running ReminderJob...")
    now = timezone.now()
    from apps.core.tenants.context import set_current_tenant, reset_current_tenant

    # 1. 24-hour Reminders
    target_24h_start = now + timedelta(hours=23, minutes=45)
    target_24h_end = now + timedelta(hours=24, minutes=15)
    sessions_24h = ClassSession.all_objects.filter(
        start_at__gte=target_24h_start,
        start_at__lte=target_24h_end,
        status='scheduled'
    )
    for session in sessions_24h:
        token = set_current_tenant(session.tenant)
        try:
            bookings = session.bookings.filter(status='booked')
            for booking in bookings:
                # Check if reminder already sent to prevent duplicate
                exists = Notification.objects.filter(
                    recipient=booking.client,
                    template_key='session_reminder_24h',
                    related_entity_id=booking.id
                ).exists()
                if not exists:
                    Notification.objects.create(
                        tenant=booking.tenant,
                        recipient=booking.client,
                        channel='email',
                        template_key='session_reminder_24h',
                        related_entity_id=booking.id
                    )
                    logger.info(f"Generated 24h reminder for {booking.client.email} for session {session}")
        finally:
            reset_current_tenant(token)

    # 2. 1-hour Reminders
    target_1h_start = now + timedelta(minutes=45)
    target_1h_end = now + timedelta(minutes=75)
    sessions_1h = ClassSession.all_objects.filter(
        start_at__gte=target_1h_start,
        start_at__lte=target_1h_end,
        status='scheduled'
    )
    for session in sessions_1h:
        token = set_current_tenant(session.tenant)
        try:
            bookings = session.bookings.filter(status='booked')
            for booking in bookings:
                exists = Notification.objects.filter(
                    recipient=booking.client,
                    template_key='session_reminder_1h',
                    related_entity_id=booking.id
                ).exists()
                if not exists:
                    Notification.objects.create(
                        tenant=booking.tenant,
                        recipient=booking.client,
                        channel='sms',
                        template_key='session_reminder_1h',
                        related_entity_id=booking.id
                    )
                    logger.info(f"Generated 1h reminder for {booking.client.email} for session {session}")
        finally:
            reset_current_tenant(token)


@shared_task
def process_waitlist_promotion_job(session_id):
    """
    Event-triggered task. Also runs as a sweep periodically.
    Offers the open spot to the next waiting client on the waitlist.
    """
    logger.info(f"Running WaitlistPromotionJob for session {session_id}...")
    now = timezone.now()

    try:
        session = ClassSession.all_objects.get(id=session_id)
    except ClassSession.DoesNotExist:
        logger.error(f"ClassSession {session_id} not found.")
        return

    from apps.core.tenants.context import set_current_tenant, reset_current_tenant
    token = set_current_tenant(session.tenant)
    try:
        with transaction.atomic():
            # Sweep expired waitlist offers on this session
            expired_offers = Waitlist.objects.select_for_update().filter(
                session=session,
                status='offered',
                expires_at__lte=now
            )
            for offer in expired_offers:
                offer.status = 'expired'
                offer.save()
                logger.info(f"Waitlist offer expired for client {offer.client.email} on session {session_id}")

            session_locked = ClassSession.objects.select_for_update().get(id=session.id)
            if session_locked.status != 'scheduled':
                return

            # Check current booked count
            current_booked = session_locked.bookings.filter(status='booked').count()
            available_spots = session_locked.capacity - current_booked

            # Check if there are active offers currently pending (status='offered')
            pending_offers = session_locked.waitlists.filter(status='offered').count()
            net_available = available_spots - pending_offers

            if net_available <= 0:
                logger.info(f"No net available capacity for promotion on session {session_id}")
                return

            # Fetch the next waiting client
            next_waitlist = session_locked.waitlists.select_for_update().filter(
                status='waiting'
            ).order_by('position').first()

            if next_waitlist:
                next_waitlist.status = 'offered'
                next_waitlist.offered_at = now
                next_waitlist.expires_at = now + timedelta(hours=5)  # 5-hour offer window
                next_waitlist.save()

                # Enqueue offer notification
                Notification.objects.create(
                    tenant=next_waitlist.tenant,
                    recipient=next_waitlist.client,
                    channel='push',
                    template_key='waitlist_spot_offered',
                    related_entity_id=next_waitlist.id
                )
                logger.info(f"Promoted client {next_waitlist.client.email} to offered status for session {session_id}")
    finally:
        reset_current_tenant(token)


@shared_task
def run_no_show_marking_job():
    """
    Scheduled task. Checks sessions that have completed and marks missed bookings as no_show.
    """
    logger.info("Running NoShowMarkingJob...")
    now = timezone.now()
    from apps.core.tenants.context import set_current_tenant, reset_current_tenant

    sessions = ClassSession.all_objects.filter(
        end_at__lte=now,
        end_at__gte=now - timedelta(hours=4),
        status='scheduled'
    )

    for session in sessions:
        token = set_current_tenant(session.tenant)
        try:
            with transaction.atomic():
                session.status = 'completed'
                session.save()

                # Find bookings that were not checked in
                bookings = Booking.objects.select_for_update().filter(
                    session=session,
                    status='booked',
                    checked_in_at__isnull=True
                )
                for booking in bookings:
                    booking.status = 'no_show'
                    booking.save()
                    logger.info(f"Booking {booking.id} marked as no_show.")
        finally:
            reset_current_tenant(token)


@shared_task
def process_credit_refund_job(session_id):
    """
    Event-triggered task when a session is cancelled by the gym.
    Refunds all bookings associated with the session.
    """
    logger.info(f"Running CreditRefundJob for cancelled session {session_id}...")

    try:
        session = ClassSession.all_objects.get(id=session_id)
    except ClassSession.DoesNotExist:
        return

    from apps.core.tenants.context import set_current_tenant, reset_current_tenant
    token = set_current_tenant(session.tenant)
    try:
        with transaction.atomic():
            bookings = Booking.objects.select_for_update().filter(
                session=session
            ).exclude(status='cancelled')

            for booking in bookings:
                booking.status = 'cancelled'
                booking.save()

                # Refund credit to package
                if booking.credit_source:
                    pkg = Package.objects.select_for_update().get(id=booking.credit_source.id)
                    pkg.credits_remaining += 1
                    pkg.save()
                    logger.info(f"Refunded credit to package {pkg.id} for client {booking.client.email}")


                Notification.objects.create(
                    tenant=booking.tenant,
                    recipient=booking.client,
                    channel='email',
                    template_key='session_cancelled_refund',
                    related_entity_id=booking.id
                )
    finally:
        reset_current_tenant(token)


@shared_task
def process_substitute_broadcast_job(substitute_request_id):
    """
    Event-triggered task when a substitute request is opened.
    Broadcasts a notification to eligible staff at the session location.
    """
    logger.info(f"Running SubstituteBroadcastJob for request {substitute_request_id}...")

    try:
        sub_req = SubstituteRequest.all_objects.get(id=substitute_request_id)
    except SubstituteRequest.DoesNotExist:
        return

    session = sub_req.session
    location = session.template.location

    from apps.core.tenants.context import set_current_tenant, reset_current_tenant
    token = set_current_tenant(sub_req.tenant)
    try:
        # Eligible staff: Trainers mapped to this location
        eligible_staff = User.objects.filter(
            role__in=[UserRole.TRAINER, UserRole.GYM_MANAGER, UserRole.GYM_OWNER],
            staff_locations__location=location
        ).exclude(id=sub_req.requested_by_staff.id)

        for staff in eligible_staff:
            Notification.objects.create(
                tenant=sub_req.tenant,
                recipient=staff,
                channel='push',
                template_key='substitute_request_broadcast',
                related_entity_id=sub_req.id
            )
            logger.info(f"Substitute notification sent to {staff.email} for request {sub_req.id}")
    finally:
        reset_current_tenant(token)
