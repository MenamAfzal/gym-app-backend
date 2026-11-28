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


WEEKDAY_NAMES = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
# Reference Monday (2024-01-01 was a Monday) for anchored 7-day cycle UTC datetime interval comparisons
_REF_MONDAY = timezone.datetime(2024, 1, 1).date()


def build_utc_interval_for_start_date(start_date, start_time, end_time):
    """
    Constructs a continuous UTC-aware (start_dt, end_dt) interval starting on `start_date` at `start_time`.
    When `end_time < start_time`, `end_dt` belongs to `start_date + timedelta(days=1)`.
    """
    from datetime import datetime as dt_cls, timedelta, timezone as dt_timezone
    start_dt = dt_cls.combine(start_date, start_time, tzinfo=dt_timezone.utc)
    end_date = start_date + timedelta(days=1) if end_time < start_time else start_date
    end_dt = dt_cls.combine(end_date, end_time, tzinfo=dt_timezone.utc)
    return start_dt, end_dt


def _parse_weekday_or_date(val_str: str):
    """
    Returns ('weekday', int_0_to_6) if `val_str` is a weekday name,
    or ('date', datetime.date) if `val_str` is a YYYY-MM-DD string,
    or (None, None) if invalid.
    """
    if not val_str:
        return None, None
    cleaned = str(val_str).strip().lower()
    if cleaned in WEEKDAY_NAMES:
        return 'weekday', WEEKDAY_NAMES.index(cleaned)
    try:
        from datetime import datetime as dt_cls
        return 'date', dt_cls.strptime(cleaned, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return None, None


def get_candidate_intervals_for_target_date(weekday_or_date: str, start_time, end_time, target_date):
    """
    Given an availability record `(weekday_or_date, start_time, end_time)` and a `target_date` (UTC date),
    returns all UTC `(start_dt, end_dt)` intervals created by that rule that could cover or overlap `target_date`.
    Checks both `target_date` (for intervals starting on `target_date`) and `target_date - 1 day`
    (for cross-midnight intervals starting the previous evening and extending into `target_date`).
    """
    from datetime import timedelta
    kind, val = _parse_weekday_or_date(weekday_or_date)
    if not kind:
        return []

    intervals = []
    for candidate_start_date in (target_date - timedelta(days=1), target_date):
        if kind == 'weekday' and candidate_start_date.weekday() == val:
            intervals.append(build_utc_interval_for_start_date(candidate_start_date, start_time, end_time))
        elif kind == 'date' and candidate_start_date == val:
            intervals.append(build_utc_interval_for_start_date(candidate_start_date, start_time, end_time))
    return intervals


def availability_records_overlap(
    weekday_or_date_1: str, start_time_1, end_time_1,
    weekday_or_date_2: str, start_time_2, end_time_2,
) -> bool:
    """
    Determines whether two availability/blackout specifications overlap in UTC time,
    properly accounting for cross-midnight intervals (`end_time < start_time`) that spill into the next day.
    """
    from datetime import timedelta
    kind1, val1 = _parse_weekday_or_date(weekday_or_date_1)
    kind2, val2 = _parse_weekday_or_date(weekday_or_date_2)
    if not kind1 or not kind2:
        return False

    if kind1 == 'date' and kind2 == 'date':
        s1, e1 = build_utc_interval_for_start_date(val1, start_time_1, end_time_1)
        s2, e2 = build_utc_interval_for_start_date(val2, start_time_2, end_time_2)
        return s1 < e2 and e1 > s2

    if kind1 == 'weekday' and kind2 == 'weekday':
        # Map both onto a 2-week reference cycle so Sunday->Monday wrap-around is also caught
        d1 = _REF_MONDAY + timedelta(days=val1)
        s1, e1 = build_utc_interval_for_start_date(d1, start_time_1, end_time_1)
        for week_offset in (-7, 0, 7):
            d2 = _REF_MONDAY + timedelta(days=val2 + week_offset)
            s2, e2 = build_utc_interval_for_start_date(d2, start_time_2, end_time_2)
            if s1 < e2 and e1 > s2:
                return True
        return False

    # One is 'date' and one is 'weekday' — only compare when both are explicit same-kind in duplicate/overlap check,
    # or check if the weekday instance around the specific date overlaps.
    return False


def _locations_match(avail_location_id, target_location_id) -> bool:
    """
    Returns True if an availability record applies to the target location:
    - If either has no location set (None), they apply globally / match.
    - If both have a location set, their IDs must match.
    """
    if avail_location_id is None or target_location_id is None:
        return True
    return str(avail_location_id) == str(target_location_id)


def _interval_overlaps(start1, end1, start2, end2) -> bool:
    return start1 < end2 and end1 > start2


def _interval_covers(slot_start, slot_end, target_start, target_end) -> bool:
    return slot_start <= target_start and slot_end >= target_end


def _intervals_cover_target(intervals, target_start, target_end) -> bool:
    """
    Checks whether `target_start..target_end` is covered by one interval or a contiguous union of intervals.
    """
    relevant = sorted(
        [(s, e) for (s, e) in intervals if s < target_end and e > target_start],
        key=lambda x: x[0]
    )
    if not relevant:
        return False
    cur_start, cur_end = relevant[0]
    if cur_start > target_start:
        return False
    for s, e in relevant[1:]:
        if s <= cur_end:
            cur_end = max(cur_end, e)
        else:
            break
    return cur_end >= target_end


def get_tenant_staff_cooldown_delta(tenant=None, staff=None):
    """
    Returns a `datetime.timedelta` representing the tenant's configured `staff_cooldown_minutes`
    (defaults to 10 minutes).
    """
    from datetime import timedelta
    from .models import TenantBookingSettings
    from apps.core.tenants.context import get_current_tenant

    resolved_tenant = tenant or getattr(staff, 'tenant', None) or get_current_tenant()
    settings_obj = TenantBookingSettings.get_or_create_for_tenant(resolved_tenant)
    minutes = getattr(settings_obj, 'staff_cooldown_minutes', 10)
    if minutes is None or minutes < 0:
        minutes = 10
    return timedelta(minutes=int(minutes)), int(minutes)


def find_staff_blackout_overlap(staff, start_at, end_at, location=None):
    """
    Checks if `staff` has any active blackout (`StaffAvailability` with `is_blackout=True`)
    overlapping the UTC datetime window `[start_at, end_at)`.
    Returns the conflicting `(StaffAvailability, blackout_start_dt, blackout_end_dt)` tuple if found, else None.
    """
    if not staff or not start_at or not end_at:
        return None
    from datetime import timedelta
    from .models import StaffAvailability

    location_id = getattr(location, 'id', location) if location is not None else None
    blackouts = list(StaffAvailability.all_objects.filter(staff=staff, is_blackout=True))
    if not blackouts:
        return None

    # Check all calendar dates spanned by [start_at, end_at]
    check_dates = set()
    cur_date = start_at.date()
    end_date = end_at.date()
    while cur_date <= end_date:
        check_dates.add(cur_date)
        cur_date += timedelta(days=1)

    for b in blackouts:
        if not _locations_match(b.location_id, location_id):
            continue
        for target_d in sorted(check_dates):
            for b_start, b_end in get_candidate_intervals_for_target_date(b.weekday_or_date, b.start_time, b.end_time, target_d):
                if _interval_overlaps(start_at, end_at, b_start, b_end):
                    return b, b_start, b_end
    return None


def check_staff_schedule_and_cooldown_conflict(
    *,
    staff,
    start_at,
    end_at,
    tenant=None,
    location=None,
    exclude_session_id=None,
    exclude_appointment_id=None,
    check_blackout: bool = True,
):
    """
    Checks whether assigning `staff` to a booking from `start_at` to `end_at` violates:
    1. An active staff blackout / unavailability (`is_blackout=True`).
    2. Direct overlap with an existing `ClassSession` or `Appointment`.
    3. The tenant's configurable `staff_cooldown_minutes` buffer between consecutive
       sessions and appointments (Session->Session, Session->Appointment,
       Appointment->Session, Appointment->Appointment).

    Returns a dictionary `{"type": ..., "detail": ...}` if a conflict exists, or `None` if valid.
    """
    if not staff or not start_at or not end_at:
        return None

    from .models import Appointment, ClassSession

    # 1. Check staff blackout / unavailability
    if check_blackout:
        blackout_hit = find_staff_blackout_overlap(staff, start_at, end_at, location=location)
        if blackout_hit:
            b_obj, b_start, b_end = blackout_hit
            return {
                "type": "blackout",
                "detail": (
                    f"This staff member is marked unavailable (blackout from "
                    f"{b_start.strftime('%H:%M')} to {b_end.strftime('%H:%M')} UTC) during the selected time slot."
                ),
            }

    cooldown_delta, cooldown_minutes = get_tenant_staff_cooldown_delta(tenant=tenant, staff=staff)

    # 2. Direct overlap check first (exact collision without cooldown across all locations)
    sess_overlap_qs = ClassSession.all_objects.filter(
        staff=staff,
        start_at__lt=end_at,
        end_at__gt=start_at,
    ).exclude(status='cancelled')
    if exclude_session_id:
        sess_overlap_qs = sess_overlap_qs.exclude(id=exclude_session_id)
    if sess_overlap_qs.exists():
        return {
            "type": "session_overlap",
            "detail": (
                "This staff member is already assigned to another session during the selected time. "
                "Please select another time slot or assign a different staff member."
            ),
        }

    appt_overlap_qs = Appointment.all_objects.filter(
        provider=staff,
        start_at__lt=end_at,
        end_at__gt=start_at,
    ).exclude(status='cancelled')
    if exclude_appointment_id:
        appt_overlap_qs = appt_overlap_qs.exclude(id=exclude_appointment_id)
    if appt_overlap_qs.exists():
        return {
            "type": "appointment_overlap",
            "detail": (
                "This staff member already has an appointment scheduled during the selected time. "
                "Please select another time slot or assign a different staff member."
            ),
        }

    # 3. Cooldown buffer check: [start_at - cooldown_delta, end_at + cooldown_delta]
    if cooldown_minutes > 0:
        buffered_start = start_at - cooldown_delta
        buffered_end = end_at + cooldown_delta

        sess_cooldown_qs = ClassSession.all_objects.filter(
            staff=staff,
            start_at__lt=buffered_end,
            end_at__gt=buffered_start,
        ).exclude(status='cancelled')
        if exclude_session_id:
            sess_cooldown_qs = sess_cooldown_qs.exclude(id=exclude_session_id)
        cooldown_sess = sess_cooldown_qs.order_by('start_at').first()
        if cooldown_sess:
            return {
                "type": "cooldown_session",
                "detail": (
                    f"Cooldown conflict: A {cooldown_minutes}-minute cooldown period is required between consecutive bookings. "
                    f"This staff member has a session ({cooldown_sess.start_at.strftime('%H:%M')} - {cooldown_sess.end_at.strftime('%H:%M')} UTC)."
                ),
            }

        appt_cooldown_qs = Appointment.all_objects.filter(
            provider=staff,
            start_at__lt=buffered_end,
            end_at__gt=buffered_start,
        ).exclude(status='cancelled')
        if exclude_appointment_id:
            appt_cooldown_qs = appt_cooldown_qs.exclude(id=exclude_appointment_id)
        cooldown_appt = appt_cooldown_qs.order_by('start_at').first()
        if cooldown_appt:
            return {
                "type": "cooldown_appointment",
                "detail": (
                    f"Cooldown conflict: A {cooldown_minutes}-minute cooldown period is required between consecutive bookings. "
                    f"This staff member has an appointment ({cooldown_appt.start_at.strftime('%H:%M')} - {cooldown_appt.end_at.strftime('%H:%M')} UTC)."
                ),
            }

    return None


def check_staff_availability_conflict(
    *,
    staff,
    weekday_or_date: str,
    start_time,
    end_time,
    is_blackout: bool = False,
    location=None,
    action: str = 'create',
    current_instance=None,
):
    """
    Validates whether creating, updating, or deleting a staff availability window:
    1. Has equal start_time and end_time (rejected).
    2. Duplicates or overlaps an existing availability/blackout window for the same staff and location.
    3. Conflicts with any upcoming active 1-on-1 appointments or class sessions for the staff
       (including appointments/classes falling on the following-day portion of a cross-midnight interval).

    Raises rest_framework.exceptions.ValidationError if a conflict is detected.
    """
    if not staff:
        return

    from datetime import timedelta
    from rest_framework.exceptions import ValidationError as DRFValidationError
    from .models import Appointment, ClassSession, StaffAvailability

    now = timezone.now()
    location_id = getattr(location, 'id', location) if location is not None else (
        getattr(current_instance, 'location_id', None) if current_instance else None
    )

    # 1. If deleting an existing blackout, that only expands availability — always allowed.
    if action == 'delete' and current_instance and current_instance.is_blackout:
        return

    # 2. Validate start_time != end_time and check duplicate / overlapping windows
    if action in ('create', 'update') and weekday_or_date and start_time is not None and end_time is not None:
        if start_time == end_time:
            raise DRFValidationError({
                "end_time": "Start time and end time cannot be equal."
            })

        existing_qs = StaffAvailability.all_objects.filter(
            staff=staff,
            is_blackout=is_blackout,
        )
        if location_id is not None:
            existing_qs = existing_qs.filter(location_id=location_id)
        else:
            existing_qs = existing_qs.filter(location__isnull=True)

        if current_instance and getattr(current_instance, 'pk', None):
            existing_qs = existing_qs.exclude(pk=current_instance.pk)

        cleaned_day = str(weekday_or_date).strip()

        # Exact duplicate check
        if existing_qs.filter(
            weekday_or_date__iexact=cleaned_day,
            start_time=start_time,
            end_time=end_time,
        ).exists():
            slot_type = "blackout" if is_blackout else "availability"
            raise DRFValidationError({
                "detail": (
                    f"Duplicate {slot_type}: This staff member already has a schedule from "
                    f"{start_time.strftime('%I:%M %p')} to {end_time.strftime('%I:%M %p')} on {weekday_or_date}."
                )
            })

        # Cross-midnight & same-day interval overlap check across existing records of the same kind (weekday or date)
        for existing in existing_qs:
            if availability_records_overlap(
                cleaned_day, start_time, end_time,
                existing.weekday_or_date, existing.start_time, existing.end_time,
            ):
                slot_type = "blackout" if is_blackout else "availability"
                raise DRFValidationError({
                    "detail": (
                        f"Overlapping {slot_type}: This staff member already has a schedule from "
                        f"{existing.start_time.strftime('%I:%M %p')} to {existing.end_time.strftime('%I:%M %p')} "
                        f"on {existing.weekday_or_date} which overlaps with "
                        f"{start_time.strftime('%I:%M %p')} - {end_time.strftime('%I:%M %p')} on {weekday_or_date}."
                    )
                })

    # 3. If creating normal availability (non-blackout), that only adds availability — no appointment conflict.
    if action == 'create' and not is_blackout:
        return

    # Fetch active upcoming appointments and class sessions for this staff member
    appts_qs = Appointment.all_objects.filter(
        provider=staff,
        status='scheduled',
        start_at__gte=now,
    ).select_related('client', 'client__profile', 'location')
    if location_id is not None:
        appts_qs = appts_qs.filter(location_id=location_id)
    appointments = list(appts_qs)

    sessions_qs = ClassSession.all_objects.filter(
        staff=staff,
        status='scheduled',
        start_at__gte=now,
    ).select_related('template', 'template__location')
    if location_id is not None:
        sessions_qs = sessions_qs.filter(template__location_id=location_id)
    class_sessions = list(sessions_qs)

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
            appt_start_dt = appt.start_at
            appt_end_dt = appt.end_at if appt.end_at else (appt.start_at + timedelta(hours=1))
            candidate_intervals = get_candidate_intervals_for_target_date(
                weekday_or_date, start_time, end_time, appt_start_dt.date()
            )
            if any(_interval_overlaps(appt_start_dt, appt_end_dt, s_dt, e_dt) for s_dt, e_dt in candidate_intervals):
                client_name = _get_client_name(appt.client)
                time_str = _format_time(appt.start_at)
                raise DRFValidationError({
                    "detail": f"Cannot add blackout: You have an active appointment with {client_name} on {time_str}. Please cancel or reschedule the appointment first."
                })

        for session in class_sessions:
            sess_start_dt = session.start_at
            sess_end_dt = session.end_at if session.end_at else (session.start_at + timedelta(hours=1))
            candidate_intervals = get_candidate_intervals_for_target_date(
                weekday_or_date, start_time, end_time, sess_start_dt.date()
            )
            if any(_interval_overlaps(sess_start_dt, sess_end_dt, s_dt, e_dt) for s_dt, e_dt in candidate_intervals):
                class_name = session.template.name if session.template else "Class"
                time_str = _format_time(session.start_at)
                raise DRFValidationError({
                    "detail": f"Cannot add blackout: You are scheduled to lead '{class_name}' on {time_str}. Please request a substitute or cancel the class session first."
                })
        return

    # Case B: Deleting an existing availability slot (which was non-blackout)
    if action == 'delete' and current_instance:
        other_slots_qs = StaffAvailability.all_objects.filter(
            staff=staff,
            is_blackout=False,
        ).exclude(id=current_instance.id)
        other_slots = [
            s for s in other_slots_qs
            if _locations_match(s.location_id, location_id)
        ]

        for appt in appointments:
            appt_start_dt = appt.start_at
            appt_end_dt = appt.end_at if appt.end_at else (appt.start_at + timedelta(hours=1))
            current_intervals = get_candidate_intervals_for_target_date(
                current_instance.weekday_or_date, current_instance.start_time, current_instance.end_time, appt_start_dt.date()
            )
            if any(_interval_overlaps(appt_start_dt, appt_end_dt, s_dt, e_dt) for s_dt, e_dt in current_intervals):
                other_intervals = []
                for s in other_slots:
                    if _locations_match(s.location_id, appt.location_id):
                        other_intervals.extend(
                            get_candidate_intervals_for_target_date(s.weekday_or_date, s.start_time, s.end_time, appt_start_dt.date())
                        )
                if not _intervals_cover_target(other_intervals, appt_start_dt, appt_end_dt):
                    client_name = _get_client_name(appt.client)
                    time_str = _format_time(appt.start_at)
                    raise DRFValidationError({
                        "detail": f"Cannot delete availability: You have an active appointment with {client_name} on {time_str}. Please cancel or reschedule the appointment first."
                    })

        for session in class_sessions:
            sess_start_dt = session.start_at
            sess_end_dt = session.end_at if session.end_at else (session.start_at + timedelta(hours=1))
            current_intervals = get_candidate_intervals_for_target_date(
                current_instance.weekday_or_date, current_instance.start_time, current_instance.end_time, sess_start_dt.date()
            )
            if any(_interval_overlaps(sess_start_dt, sess_end_dt, s_dt, e_dt) for s_dt, e_dt in current_intervals):
                sess_loc_id = session.template.location_id if session.template else None
                other_intervals = []
                for s in other_slots:
                    if _locations_match(s.location_id, sess_loc_id):
                        other_intervals.extend(
                            get_candidate_intervals_for_target_date(s.weekday_or_date, s.start_time, s.end_time, sess_start_dt.date())
                        )
                if not _intervals_cover_target(other_intervals, sess_start_dt, sess_end_dt):
                    class_name = session.template.name if session.template else "Class"
                    time_str = _format_time(session.start_at)
                    raise DRFValidationError({
                        "detail": f"Cannot delete availability: You are scheduled to lead '{class_name}' on {time_str}. Please request a substitute or cancel the class session first."
                    })
        return

    # Case C: Updating an existing availability slot (modifying times, weekday, etc.)
    if action == 'update' and current_instance:
        other_slots_qs = StaffAvailability.all_objects.filter(
            staff=staff,
            is_blackout=False,
        ).exclude(id=current_instance.id)
        other_slots = [
            s for s in other_slots_qs
            if _locations_match(s.location_id, location_id)
        ]

        for appt in appointments:
            appt_start_dt = appt.start_at
            appt_end_dt = appt.end_at if appt.end_at else (appt.start_at + timedelta(hours=1))
            old_intervals = get_candidate_intervals_for_target_date(
                current_instance.weekday_or_date, current_instance.start_time, current_instance.end_time, appt_start_dt.date()
            )
            if any(_interval_overlaps(appt_start_dt, appt_end_dt, s_dt, e_dt) for s_dt, e_dt in old_intervals):
                combined_intervals = []
                if not is_blackout and _locations_match(location_id, appt.location_id):
                    combined_intervals.extend(
                        get_candidate_intervals_for_target_date(weekday_or_date, start_time, end_time, appt_start_dt.date())
                    )
                for s in other_slots:
                    if _locations_match(s.location_id, appt.location_id):
                        combined_intervals.extend(
                            get_candidate_intervals_for_target_date(s.weekday_or_date, s.start_time, s.end_time, appt_start_dt.date())
                        )
                if not _intervals_cover_target(combined_intervals, appt_start_dt, appt_end_dt):
                    client_name = _get_client_name(appt.client)
                    time_str = _format_time(appt.start_at)
                    raise DRFValidationError({
                        "detail": f"Cannot update availability: You have an active appointment with {client_name} on {time_str}. Please cancel or reschedule the appointment first."
                    })

        for session in class_sessions:
            sess_start_dt = session.start_at
            sess_end_dt = session.end_at if session.end_at else (session.start_at + timedelta(hours=1))
            old_intervals = get_candidate_intervals_for_target_date(
                current_instance.weekday_or_date, current_instance.start_time, current_instance.end_time, sess_start_dt.date()
            )
            if any(_interval_overlaps(sess_start_dt, sess_end_dt, s_dt, e_dt) for s_dt, e_dt in old_intervals):
                sess_loc_id = session.template.location_id if session.template else None
                combined_intervals = []
                if not is_blackout and _locations_match(location_id, sess_loc_id):
                    combined_intervals.extend(
                        get_candidate_intervals_for_target_date(weekday_or_date, start_time, end_time, sess_start_dt.date())
                    )
                for s in other_slots:
                    if _locations_match(s.location_id, sess_loc_id):
                        combined_intervals.extend(
                            get_candidate_intervals_for_target_date(s.weekday_or_date, s.start_time, s.end_time, sess_start_dt.date())
                        )
                if not _intervals_cover_target(combined_intervals, sess_start_dt, sess_end_dt):
                    class_name = session.template.name if session.template else "Class"
                    time_str = _format_time(session.start_at)
                    raise DRFValidationError({
                        "detail": f"Cannot update availability: You are scheduled to lead '{class_name}' on {time_str}. Please request a substitute or cancel the class session first."
                    })
        return

