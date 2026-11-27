import logging
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import RoomLayout, Spot, SpotType, ClassSession, Booking

logger = logging.getLogger(__name__)


class SpotUnavailableError(ValidationError):
    """Raised when a requested spot is already booked or blocked."""
    pass


@transaction.atomic
def create_layout(*, tenant, room, name, grid_rows, grid_cols, spots_data) -> RoomLayout:
    """
    Atomically creates a RoomLayout and its child spots with stable per-type numbering.
    Optimized with single-pass validation and bulk creation to prevent N+1 queries.
    """
    if grid_rows <= 0 or grid_cols <= 0:
        raise ValidationError("Grid rows and columns must be positive integers.")

    layout = RoomLayout.objects.create(
        tenant=tenant,
        room=room,
        name=name,
        grid_rows=grid_rows,
        grid_cols=grid_cols,
        version=1
    )

    seen_coords = set()
    spot_type_ids = {str(s['spot_type'].id if hasattr(s['spot_type'], 'id') else s['spot_type']) for s in spots_data}
     
    spot_types_map = {
        str(st.id): st for st in SpotType.objects.filter(id__in=spot_type_ids, location=room.location)
    }

    spots_to_create = []
    counters = {}

    for s in spots_data:
        r = s['row']
        c = s['col']
        if r < 0 or r >= grid_rows or c < 0 or c >= grid_cols:
            raise ValidationError(f"Spot at ({r}, {c}) is outside grid boundaries ({grid_rows}x{grid_cols}).")

        coord = (r, c)
        if coord in seen_coords:
            raise ValidationError(f"Duplicate spot position at row {r}, col {c}.")
        seen_coords.add(coord)

        raw_st = s['spot_type']
        st_id = str(raw_st.id if hasattr(raw_st, 'id') else raw_st)
        st = spot_types_map.get(st_id)
        if not st:
            raise ValidationError(f"Invalid spot type {st_id} for this room's location.")

        counters[st_id] = counters.get(st_id, 0) + 1
        num = counters[st_id]
        label = f"{st.prefix}{num}"

        spots_to_create.append(
            Spot(
                tenant=tenant,
                layout=layout,
                spot_type=st,
                row=r,
                col=c,
                number=num,
                label=label,
                is_blocked=s.get('is_blocked', False)
            )
        )

    # Bulk create all spots in a single SQL INSERT
    Spot.objects.bulk_create(spots_to_create)
    return layout


@transaction.atomic
def update_layout(*, layout, name=None, grid_rows=None, grid_cols=None, spots_data=None) -> RoomLayout:
    """
    Structural edit for RoomLayout:
    1. Increments layout.version (so past classes retain their pinned historical layout).
    2. Replaces spots atomically.
    3. Detects active bookings on removed spots and marks them 'unassigned'.
    4. Triggers background Celery task to auto-reassign displaced members.
    """
    from .tasks import auto_assign_unassigned

    layout.version += 1
    if name is not None:
        layout.name = name
    if grid_rows is not None:
        layout.grid_rows = grid_rows
    if grid_cols is not None:
        layout.grid_cols = grid_cols

    layout.save(update_fields=['version', 'name', 'grid_rows', 'grid_cols'])

    if spots_data is not None:
        grid_r = layout.grid_rows
        grid_c = layout.grid_cols
        seen_coords = set()
        spot_type_ids = {str(s['spot_type'].id if hasattr(s['spot_type'], 'id') else s['spot_type']) for s in spots_data}
        spot_types_map = {
            str(st.id): st for st in SpotType.objects.filter(id__in=spot_type_ids, location=layout.room.location)
        }

        # Validate spot coordinates and spot types
        for s in spots_data:
            r = s['row']
            c = s['col']
            if r < 0 or r >= grid_r or c < 0 or c >= grid_c:
                raise ValidationError(f"Spot at ({r}, {c}) is outside grid boundaries ({grid_r}x{grid_c}).")
            coord = (r, c)
            if coord in seen_coords:
                raise ValidationError(f"Duplicate spot position at row {r}, col {c}.")
            seen_coords.add(coord)

            raw_st = s['spot_type']
            st_id = str(raw_st.id if hasattr(raw_st, 'id') else raw_st)
            if st_id not in spot_types_map:
                raise ValidationError(f"Invalid spot type {st_id} for this room's location.")

        # Identify removed spots
        old_spots = list(layout.spots.all())
        old_spots_map = {
            (s.row, s.col, str(s.spot_type_id)): s
            for s in old_spots
        }
        
        new_coords_map = {
            (s['row'], s['col'], str(s['spot_type'].id if hasattr(s['spot_type'], 'id') else s['spot_type'])): s
            for s in spots_data
        }
        
        removed_spot_ids = [
            s.id for coord, s in old_spots_map.items()
            if coord not in new_coords_map
        ]

        # Mark orphaned bookings on removed spots as unassigned BEFORE deleting spots
        if removed_spot_ids:
            orphaned = Booking.objects.filter(
                spot_id__in=removed_spot_ids,
                status='booked',
                session__status='scheduled',
                session__start_at__gte=timezone.now()
            )
            orphaned.update(status='unassigned', spot=None)
            try:
                auto_assign_unassigned.delay(str(layout.id))
            except Exception as e:
                logger.warning(f"Failed to queue auto_assign_unassigned for layout {layout.id}: {e}")

        # Delete ONLY removed spots
        if removed_spot_ids:
            Spot.objects.filter(id__in=removed_spot_ids).delete()

        # Find max number per spot type to continue sequential numbering for new spots
        counters = {}
        for s in old_spots:
            st_id = str(s.spot_type_id)
            counters[st_id] = max(counters.get(st_id, 0), s.number)

        # Build and bulk create new spots, update existing spots if needed
        spots_to_create = []
        for coord, s in new_coords_map.items():
            if coord not in old_spots_map:
                # This is a new spot, assign next sequential number
                raw_st = s['spot_type']
                st_id = str(raw_st.id if hasattr(raw_st, 'id') else raw_st)
                st = spot_types_map[st_id]
                
                counters[st_id] = counters.get(st_id, 0) + 1
                num = counters[st_id]
                label = f"{st.prefix}{num}"

                spots_to_create.append(
                    Spot(
                        tenant=layout.tenant,
                        layout=layout,
                        spot_type=st,
                        row=s['row'],
                        col=s['col'],
                        number=num,
                        label=label,
                        is_blocked=s.get('is_blocked', False)
                    )
                )
            else:
                # Existing spot, retain original number and label, just update properties if changed
                existing_spot = old_spots_map[coord]
                is_blocked = s.get('is_blocked', False)
                if existing_spot.is_blocked != is_blocked:
                    existing_spot.is_blocked = is_blocked
                    existing_spot.save(update_fields=['is_blocked'])

        if spots_to_create:
            Spot.objects.bulk_create(spots_to_create)

    return layout


def validate_event_capacity(*, capacity, layout):
    """
    Enforces Glofox business rule: class capacity cannot exceed layout bookable capacity.
    If no layout is assigned, validation is skipped (room/layout is optional).
    """
    if layout:
        layout_cap = layout.capacity
        if capacity > layout_cap:
            raise ValidationError(f"Class capacity ({capacity}) exceeds layout capacity ({layout_cap}).")


@transaction.atomic
def change_booking_spot(*, booking, new_spot_id):
    """
    Atomically moves a booking to a new spot before class start.
    Prevents race conditions using select_for_update on the session.
    """
    session = ClassSession.objects.select_for_update().get(id=booking.session_id)
    if session.status != 'scheduled':
        raise ValidationError("Cannot change spot for a session that is not scheduled.")

    if session.start_at <= timezone.now():
        raise ValidationError("Cannot change spot after class start time.")

    if not session.layout:
        raise ValidationError("This class does not have an assigned room layout.")

    try:
        new_spot = Spot.objects.select_related('spot_type').get(id=new_spot_id, layout=session.layout)
    except Spot.DoesNotExist:
        raise ValidationError("Selected spot does not exist in this class's room layout.")

    if not new_spot.spot_type.is_bookable:
        raise ValidationError("Selected spot is non-bookable.")

    if new_spot.is_blocked:
        raise ValidationError("Selected spot is permanently blocked in layout.")

    # Check per-session temporary blocks (JSONB)
    blocked_list = [str(b) for b in (session.blocked_spots or [])]
    if str(new_spot.id) in blocked_list:
        raise ValidationError("Selected spot is blocked for this class occurrence.")

    # Check if another active booking already holds this spot
    is_taken = Booking.objects.filter(
        session=session,
        spot=new_spot,
        status='booked'
    ).exclude(id=booking.id).exists()

    if is_taken:
        raise SpotUnavailableError("That spot was just taken. Please choose another spot.")

    booking.spot = new_spot
    booking.save(update_fields=['spot'])
    return booking


def _matches_date_or_weekday(val_str: str, target_dt) -> bool:
    if not val_str or not target_dt:
        return False
    cleaned = str(val_str).strip().lower()
    if cleaned in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
        return target_dt.strftime('%A').lower() == cleaned
    try:
        from datetime import datetime as dt_cls
        parsed_date = dt_cls.strptime(cleaned, '%Y-%m-%d').date()
        return target_dt.date() == parsed_date
    except (ValueError, TypeError):
        return False


def _times_overlap(start1, end1, start2, end2) -> bool:
    return start1 < end2 and end1 > start2


def _slot_covers_time(slot_start, slot_end, target_start, target_end) -> bool:
    return slot_start <= target_start and slot_end >= target_end


def check_staff_availability_conflict(
    *,
    staff,
    weekday_or_date: str,
    start_time,
    end_time,
    is_blackout: bool = False,
    action: str = 'create',
    current_instance=None,
):
    """
    Validates whether changing or deleting a staff availability window conflicts
    with any upcoming active 1-on-1 appointments or class sessions for the staff.

    Option 1: Prevents staff from removing availability or setting blackouts
    over times where clients have already booked appointments.

    Raises rest_framework.exceptions.ValidationError if a conflict is detected.
    """
    if not staff:
        return

    from datetime import timedelta
    from rest_framework.exceptions import ValidationError as DRFValidationError
    from .models import Appointment, ClassSession, StaffAvailability

    now = timezone.now()

    # 1. If deleting an existing blackout, that only expands availability — always allowed.
    if action == 'delete' and current_instance and current_instance.is_blackout:
        return

    # 2. Prevent duplicate or overlapping availability/blackout slots for the same staff & weekday/date
    if action in ('create', 'update') and weekday_or_date and start_time and end_time:
        if start_time >= end_time:
            raise DRFValidationError({"end_time": "End time must be after start time."})

        existing_qs = StaffAvailability.all_objects.filter(
            staff=staff,
            weekday_or_date__iexact=str(weekday_or_date).strip(),
            is_blackout=is_blackout,
        )
        if current_instance and getattr(current_instance, 'pk', None):
            existing_qs = existing_qs.exclude(pk=current_instance.pk)

        if existing_qs.filter(start_time=start_time, end_time=end_time).exists():
            slot_type = "blackout" if is_blackout else "availability"
            raise DRFValidationError({
                "detail": (
                    f"Duplicate {slot_type}: This staff member already has a schedule from "
                    f"{start_time.strftime('%I:%M %p')} to {end_time.strftime('%I:%M %p')} on {weekday_or_date}."
                )
            })

        overlapping = existing_qs.filter(
            start_time__lt=end_time,
            end_time__gt=start_time,
        ).first()
        if overlapping:
            slot_type = "blackout" if is_blackout else "availability"
            raise DRFValidationError({
                "detail": (
                    f"Overlapping {slot_type}: This staff member already has a schedule from "
                    f"{overlapping.start_time.strftime('%I:%M %p')} to {overlapping.end_time.strftime('%I:%M %p')} "
                    f"on {weekday_or_date} which overlaps with {start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')}."
                )
            })

    # 3. If creating normal availability (non-blackout), that only adds availability — no appointment conflict.
    if action == 'create' and not is_blackout:
        return

    # Fetch active upcoming appointments and class sessions for this staff member
    appointments = list(
        Appointment.all_objects.filter(
            provider=staff,
            status='scheduled',
            start_at__gte=now,
        ).select_related('client', 'client__profile')
    )

    class_sessions = list(
        ClassSession.all_objects.filter(
            staff=staff,
            status='scheduled',
            start_at__gte=now,
        ).select_related('template')
    )

    if not appointments and not class_sessions:
        return

    def _get_client_name(client):
        if hasattr(client, 'profile') and client.profile and client.profile.first_name:
            full = client.profile.first_name
            if client.profile.last_name:
                full += f" {client.profile.last_name}"
            return full
        return getattr(client, 'email', 'Client')

    def _format_time(dt):
        return dt.strftime('%A, %b %d at %I:%M %p')

    # Case A: Creating a new blackout OR updating an existing slot to a blackout
    if is_blackout:
        for appt in appointments:
            appt_end = appt.end_at.time() if appt.end_at else (appt.start_at + timedelta(hours=1)).time()
            appt_start = appt.start_at.time()
            if _matches_date_or_weekday(weekday_or_date, appt.start_at) and _times_overlap(appt_start, appt_end, start_time, end_time):
                client_name = _get_client_name(appt.client)
                time_str = _format_time(appt.start_at)
                raise DRFValidationError({
                    "detail": f"Cannot add blackout: You have an active appointment with {client_name} on {time_str}. Please cancel or reschedule the appointment first."
                })

        for session in class_sessions:
            sess_end = session.end_at.time() if session.end_at else (session.start_at + timedelta(hours=1)).time()
            sess_start = session.start_at.time()
            if _matches_date_or_weekday(weekday_or_date, session.start_at) and _times_overlap(sess_start, sess_end, start_time, end_time):
                class_name = session.template.name if session.template else "Class"
                time_str = _format_time(session.start_at)
                raise DRFValidationError({
                    "detail": f"Cannot add blackout: You are scheduled to lead '{class_name}' on {time_str}. Please request a substitute or cancel the class session first."
                })
        return

    # Case B: Deleting an existing availability slot (which was non-blackout)
    if action == 'delete' and current_instance:
        other_slots = list(
            StaffAvailability.objects.filter(
                staff=staff,
                is_blackout=False,
            ).exclude(id=current_instance.id)
        )

        for appt in appointments:
            appt_end = appt.end_at.time() if appt.end_at else (appt.start_at + timedelta(hours=1)).time()
            appt_start = appt.start_at.time()
            # Did this slot cover the appointment?
            if _matches_date_or_weekday(current_instance.weekday_or_date, appt.start_at) and _times_overlap(appt_start, appt_end, current_instance.start_time, current_instance.end_time):
                # Is it covered by any other remaining availability slot?
                covered = any(
                    _matches_date_or_weekday(s.weekday_or_date, appt.start_at) and _slot_covers_time(s.start_time, s.end_time, appt_start, appt_end)
                    for s in other_slots
                )
                if not covered:
                    client_name = _get_client_name(appt.client)
                    time_str = _format_time(appt.start_at)
                    raise DRFValidationError({
                        "detail": f"Cannot delete availability: You have an active appointment with {client_name} on {time_str}. Please cancel or reschedule the appointment first."
                    })

        for session in class_sessions:
            sess_end = session.end_at.time() if session.end_at else (session.start_at + timedelta(hours=1)).time()
            sess_start = session.start_at.time()
            if _matches_date_or_weekday(current_instance.weekday_or_date, session.start_at) and _times_overlap(sess_start, sess_end, current_instance.start_time, current_instance.end_time):
                covered = any(
                    _matches_date_or_weekday(s.weekday_or_date, session.start_at) and _slot_covers_time(s.start_time, s.end_time, sess_start, sess_end)
                    for s in other_slots
                )
                if not covered:
                    class_name = session.template.name if session.template else "Class"
                    time_str = _format_time(session.start_at)
                    raise DRFValidationError({
                        "detail": f"Cannot delete availability: You are scheduled to lead '{class_name}' on {time_str}. Please request a substitute or cancel the class session first."
                    })
        return

    # Case C: Updating an existing availability slot (modifying times, weekday, etc.)
    if action == 'update' and current_instance:
        other_slots = list(
            StaffAvailability.objects.filter(
                staff=staff,
                is_blackout=False,
            ).exclude(id=current_instance.id)
        )

        for appt in appointments:
            appt_end = appt.end_at.time() if appt.end_at else (appt.start_at + timedelta(hours=1)).time()
            appt_start = appt.start_at.time()
            # Was it covered or overlapped by the old slot?
            if _matches_date_or_weekday(current_instance.weekday_or_date, appt.start_at) and _times_overlap(appt_start, appt_end, current_instance.start_time, current_instance.end_time):
                # Is it covered by the new updated slot?
                covered_by_new = (
                    not is_blackout and
                    _matches_date_or_weekday(weekday_or_date, appt.start_at) and
                    _slot_covers_time(start_time, end_time, appt_start, appt_end)
                )
                # Or covered by any other existing slot?
                covered_by_other = any(
                    _matches_date_or_weekday(s.weekday_or_date, appt.start_at) and _slot_covers_time(s.start_time, s.end_time, appt_start, appt_end)
                    for s in other_slots
                )
                if not covered_by_new and not covered_by_other:
                    client_name = _get_client_name(appt.client)
                    time_str = _format_time(appt.start_at)
                    raise DRFValidationError({
                        "detail": f"Cannot update availability: You have an active appointment with {client_name} on {time_str}. Please cancel or reschedule the appointment first."
                    })

        for session in class_sessions:
            sess_end = session.end_at.time() if session.end_at else (session.start_at + timedelta(hours=1)).time()
            sess_start = session.start_at.time()
            if _matches_date_or_weekday(current_instance.weekday_or_date, session.start_at) and _times_overlap(sess_start, sess_end, current_instance.start_time, current_instance.end_time):
                covered_by_new = (
                    not is_blackout and
                    _matches_date_or_weekday(weekday_or_date, session.start_at) and
                    _slot_covers_time(start_time, end_time, sess_start, sess_end)
                )
                covered_by_other = any(
                    _matches_date_or_weekday(s.weekday_or_date, session.start_at) and _slot_covers_time(s.start_time, s.end_time, sess_start, sess_end)
                    for s in other_slots
                )
                if not covered_by_new and not covered_by_other:
                    class_name = session.template.name if session.template else "Class"
                    time_str = _format_time(session.start_at)
                    raise DRFValidationError({
                        "detail": f"Cannot update availability: You are scheduled to lead '{class_name}' on {time_str}. Please request a substitute or cancel the class session first."
                    })
        return

