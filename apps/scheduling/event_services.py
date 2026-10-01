import logging
from datetime import timedelta
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Event, EventSession, EventEnrollment, Package, Room

logger = logging.getLogger(__name__)


class EventRegistrationError(ValidationError):
    """Base error for event registration failures."""
    pass


class EventPaymentRequiredError(EventRegistrationError):
    """Raised when client does not have an active pass or credits for a paid event."""
    def __init__(self, message="An active client pass with remaining credits is required to enroll in this event.", code=402):
        super().__init__(message)
        self.status_code = code


class EventCapacityError(EventRegistrationError):
    """Raised when event is at capacity and waitlist is full or unavailable."""
    pass


def create_event_session(*, tenant, event, session_data=None, index=1) -> EventSession:
    if not isinstance(session_data, dict):
        session_data = {}

    title = session_data.get('title') or (event.title if index == 1 else f"Session {index}")
    session_num = session_data.get('session_number', index)

    start_at = (
        session_data.get('start_at')
        or session_data.get('start')
        or session_data.get('start_time')
        or session_data.get('startTime')
    )
    end_at = (
        session_data.get('end_at')
        or session_data.get('end')
        or session_data.get('end_time')
        or session_data.get('endTime')
    )

    date_val = session_data.get('date') or session_data.get('session_date')
    if date_val and start_at and 'T' not in str(start_at) and ' ' not in str(start_at):
        start_at = f"{date_val}T{start_at}"
    if date_val and end_at and 'T' not in str(end_at) and ' ' not in str(end_at):
        end_at = f"{date_val}T{end_at}"

    if not start_at:
        start_at = event.start_at
    if not end_at:
        end_at = event.end_at or start_at

    room_val = session_data.get('room', event.room)
    room_kwargs = {}
    if isinstance(room_val, Room):
        room_kwargs['room'] = room_val
    elif room_val:
        room_kwargs['room_id'] = room_val
    else:
        room_kwargs['room'] = event.room

    instructor_val = session_data.get('instructor', event.primary_instructor)
    instructor_kwargs = {}
    from django.contrib.auth import get_user_model
    User = get_user_model()
    if isinstance(instructor_val, User):
        instructor_kwargs['instructor'] = instructor_val
    elif instructor_val:
        instructor_kwargs['instructor_id'] = instructor_val
    else:
        instructor_kwargs['instructor'] = event.primary_instructor

    return EventSession.all_objects.create(
        tenant=tenant,
        event=event,
        session_number=session_num,
        title=title,
        start_at=start_at,
        end_at=end_at,
        status='scheduled',
        **room_kwargs,
        **instructor_kwargs
    )


@transaction.atomic
def create_event(*, tenant, validated_data, sessions_data=None) -> Event:
    assistant_instructors = validated_data.pop('assistant_instructors', [])
    
    event = Event.all_objects.create(tenant=tenant, **validated_data)
    
    if assistant_instructors:
        event.assistant_instructors.set(assistant_instructors)
        
    if sessions_data:
        for idx, s_data in enumerate(sessions_data, start=1):
            create_event_session(tenant=tenant, event=event, session_data=s_data, index=idx)
    elif event.event_type == 'single':
        create_event_session(tenant=tenant, event=event, session_data={}, index=1)

    return event


@transaction.atomic
def enroll_client(*, tenant, event_id, client, is_complimentary=False, notes="") -> tuple[EventEnrollment, str]:
    """
    Mindbody-style enrollment for an Event/Workshop:
    - Paid event: Requires active client pass (Package) with credits_remaining >= credits_required.
    - Free event: No pass required.
    - Waitlist: If event full, places client on waitlist without deducting credits.
    - Concurrency-safe: uses select_for_update on both Event and Package.
    Returns: (enrollment, action_taken) where action_taken in ['registered', 'waitlisted']
    """
    # 1. Lock the event to prevent capacity race conditions
    try:
        event = Event.all_objects.select_for_update().get(id=event_id)
    except Event.DoesNotExist:
        raise ValidationError("Event does not exist.")

    # 2. Verify registration window and status
    if not event.is_registration_open:
        raise ValidationError("Registration is currently closed for this event.")

    # 3. Check existing enrollment
    existing = EventEnrollment.all_objects.filter(event=event, client=client).first()
    if existing:
        if existing.status in ['registered', 'attended']:
            raise ValidationError("Client is already enrolled in this event.")
        if existing.status == 'waitlisted':
            raise ValidationError("Client is already on the waitlist for this event.")

    current_enrollments = event.enrollments.filter(status__in=['registered', 'attended']).count()
    is_at_capacity = current_enrollments >= event.capacity

    # 4. Handle Waitlist when event is full
    if is_at_capacity:
        if event.waitlist_capacity > 0:
            current_waitlist = event.enrollments.filter(status='waitlisted').count()
            if current_waitlist < event.waitlist_capacity:
                pricing_type = 'complimentary' if is_complimentary else ('free' if event.is_free else 'pass')
                if existing:
                    existing.status = 'waitlisted'
                    existing.pricing_type = pricing_type
                    existing.credits_deducted = 0
                    existing.credit_source = None
                    existing.cancelled_at = None
                    existing.cancellation_reason = ""
                    existing.notes = notes
                    existing.save()
                    enrollment = existing
                else:
                    enrollment = EventEnrollment.all_objects.create(
                        tenant=tenant,
                        event=event,
                        client=client,
                        status='waitlisted',
                        pricing_type=pricing_type,
                        credits_deducted=0,
                        credit_source=None,
                        notes=notes
                    )
                return enrollment, 'waitlisted'
            else:
                raise EventCapacityError("Event capacity and waitlist are both completely full.")
        else:
            raise EventCapacityError("This event is at maximum capacity.")

    # 5. Handle Pass Credit Deduction for Paid Events
    package_to_charge = None
    credits_to_deduct = 0
    pricing_type = 'free'

    if is_complimentary:
        pricing_type = 'complimentary'
    elif event.is_free:
        pricing_type = 'free'
    else:
        pricing_type = 'pass'
        credits_to_deduct = event.credits_required

        package_to_charge = Package.all_objects.select_for_update().filter(
            client=client,
            status='active',
            credits_remaining__gte=credits_to_deduct,
            expires_at__gt=timezone.now(),
            package_type__location=event.location
        ).order_by('expires_at').first()

        if not package_to_charge:
            has_other = Package.all_objects.filter(
                client=client,
                status='active',
                credits_remaining__gte=credits_to_deduct,
                expires_at__gt=timezone.now()
            ).exists()
            if has_other:
                raise ValidationError("Your purchased package is not valid for this location.")
            raise EventPaymentRequiredError(
                f"Insufficient credits or no active package found. Enrolling in this event requires {credits_to_deduct} credit(s)."
            )

        package_to_charge.credits_remaining -= credits_to_deduct
        package_to_charge.save(update_fields=['credits_remaining'])

    # 6. Create or re-activate Enrollment
    if existing:
        existing.status = 'registered'
        existing.pricing_type = pricing_type
        existing.credits_deducted = credits_to_deduct
        existing.credit_source = package_to_charge
        existing.cancelled_at = None
        existing.cancellation_reason = ""
        existing.notes = notes
        existing.save()
        enrollment = existing
    else:
        enrollment = EventEnrollment.all_objects.create(
            tenant=tenant,
            event=event,
            client=client,
            status='registered',
            pricing_type=pricing_type,
            credits_deducted=credits_to_deduct,
            credit_source=package_to_charge,
            notes=notes
        )

    return enrollment, 'registered'


@transaction.atomic
def cancel_enrollment(*, enrollment_id, user, reason="") -> EventEnrollment:
    """
    Cancels an enrollment.
    - If cancelled before cancellation_cutoff_hours, refunds deducted credits back to client's pass.
    - Automatically promotes the next waitlisted client if spots become available.
    """
    try:
        enrollment = EventEnrollment.all_objects.select_for_update().get(id=enrollment_id)
    except EventEnrollment.DoesNotExist:
        raise ValidationError("Enrollment not found.")

    if enrollment.status == 'cancelled':
        raise ValidationError("Enrollment is already cancelled.")

    event = Event.all_objects.select_for_update().get(id=enrollment.event_id)
    was_registered = enrollment.status in ['registered', 'attended']
    now = timezone.now()

    # Determine eligibility for pass credit refund
    cutoff_time = event.start_at - timedelta(hours=event.cancellation_cutoff_hours)
    is_early_cancellation = now <= cutoff_time

    if was_registered and enrollment.credits_deducted > 0 and enrollment.credit_source:
        if is_early_cancellation:
            # Refund pass credits
            pkg = Package.all_objects.select_for_update().filter(id=enrollment.credit_source_id).first()
            if pkg:
                pkg.credits_remaining += enrollment.credits_deducted
                pkg.save(update_fields=['credits_remaining'])
                logger.info(
                    "Refunded %s credits to package %s for client %s on early cancellation of event %s",
                    enrollment.credits_deducted, pkg.id, enrollment.client_id, event.id
                )
        else:
            logger.info(
                "Late cancellation for client %s on event %s: no credits refunded (cutoff %s passed)",
                enrollment.client_id, event.id, cutoff_time
            )

    enrollment.status = 'cancelled'
    enrollment.cancelled_at = now
    enrollment.cancellation_reason = reason
    enrollment.save()

    # Waitlist Promotion
    if was_registered:
        promote_next_waitlist_attendee(event)

    return enrollment


@transaction.atomic
def promote_next_waitlist_attendee(event: Event) -> EventEnrollment | None:
    """
    Finds the next waitlisted client for the event and attempts to promote them to 'registered'.
    If paid, attempts to deduct from an active client pass. If client has no pass, stays on waitlist or notifies.
    """
    waitlisted = EventEnrollment.all_objects.select_for_update().filter(
        event=event,
        status='waitlisted'
    ).order_by('created_at').first()

    if not waitlisted:
        return None

    if event.is_free or waitlisted.pricing_type == 'complimentary':
        waitlisted.status = 'registered'
        waitlisted.save(update_fields=['status'])
        logger.info("Promoted waitlisted client %s to registered for free event %s", waitlisted.client_id, event.id)
        return waitlisted

    pkg = Package.all_objects.select_for_update().filter(
        client=waitlisted.client,
        status='active',
        credits_remaining__gte=event.credits_required,
        expires_at__gt=timezone.now(),
        package_type__location=event.location
    ).order_by('expires_at').first()

    if pkg:
        pkg.credits_remaining -= event.credits_required
        pkg.save(update_fields=['credits_remaining'])
        waitlisted.credit_source = pkg
        waitlisted.credits_deducted = event.credits_required
        waitlisted.pricing_type = 'pass'
        waitlisted.status = 'registered'
        waitlisted.save(update_fields=['status', 'credit_source', 'credits_deducted', 'pricing_type'])
        logger.info("Promoted waitlisted client %s with package %s for event %s", waitlisted.client_id, pkg.id, event.id)
        return waitlisted
    else:
        logger.warning(
            "Client %s is next on waitlist for event %s but lacks credits. Skipping auto-deduct.",
            waitlisted.client_id, event.id
        )
        return None


@transaction.atomic
def check_in_attendee(*, enrollment_id, event_session_id=None) -> EventEnrollment:
    """
    Staff check-in for an event attendee.
    Tracks session attendance for multi-session workshops.
    """
    try:
        enrollment = EventEnrollment.all_objects.select_for_update().get(id=enrollment_id)
    except EventEnrollment.DoesNotExist:
        raise ValidationError("Enrollment not found.")

    if enrollment.status not in ['registered', 'attended']:
        raise ValidationError(f"Cannot check in client with status '{enrollment.status}'.")

    now = timezone.now()
    enrollment.status = 'attended'
    if not enrollment.checked_in_at:
        enrollment.checked_in_at = now

    if event_session_id:
        try:
            session = EventSession.all_objects.get(id=event_session_id, event=enrollment.event)
            enrollment.attended_sessions.add(session)
        except EventSession.DoesNotExist:
            raise ValidationError("Invalid event session specified for check-in.")

    enrollment.save()
    return enrollment


@transaction.atomic
def cancel_event(*, event_id, reason="") -> Event:
    """
    Admin/Manager cancels an event.
    All registered attendees with pass credits deducted are automatically refunded.
    """
    try:
        event = Event.all_objects.select_for_update().get(id=event_id)
    except Event.DoesNotExist:
        raise ValidationError("Event not found.")

    event.status = 'cancelled'
    event.save(update_fields=['status'])

    # Refund all registered attendees
    active_enrollments = EventEnrollment.all_objects.select_for_update().filter(
        event=event,
        status__in=['registered', 'attended', 'waitlisted']
    )

    now = timezone.now()
    for enr in active_enrollments:
        if enr.credits_deducted > 0 and enr.credit_source:
            pkg = Package.all_objects.select_for_update().filter(id=enr.credit_source_id).first()
            if pkg:
                pkg.credits_remaining += enr.credits_deducted
                pkg.save(update_fields=['credits_remaining'])
        enr.status = 'cancelled'
        enr.cancelled_at = now
        enr.cancellation_reason = f"Event cancelled by host: {reason}" if reason else "Event cancelled by host."
        enr.save()

    return event
