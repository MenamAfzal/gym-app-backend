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
    spot_type_ids = {s['spot_type'].id if hasattr(s['spot_type'], 'id') else s['spot_type'] for s in spots_data}
    
    # Preload spot types in single query (prevents N+1)
    spot_types_map = {
        st.id: st for st in SpotType.objects.filter(id__in=spot_type_ids, location=room.location)
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
        st_id = raw_st.id if hasattr(raw_st, 'id') else raw_st
        st = spot_types_map.get(st_id)
        if not st:
            raise ValidationError(f"Invalid spot type {st_id} for this room's location.")

        counters[st.id] = counters.get(st.id, 0) + 1
        num = counters[st.id]
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
        spot_type_ids = {s['spot_type'].id if hasattr(s['spot_type'], 'id') else s['spot_type'] for s in spots_data}
        spot_types_map = {
            st.id: st for st in SpotType.objects.filter(id__in=spot_type_ids, location=layout.room.location)
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
            st_id = raw_st.id if hasattr(raw_st, 'id') else raw_st
            if st_id not in spot_types_map:
                raise ValidationError(f"Invalid spot type {st_id} for this room's location.")

        # Identify removed spots
        old_spots = list(layout.spots.all())
        old_spots_map = {
            (s.row, s.col, s.spot_type_id): s
            for s in old_spots
        }
        
        new_coords_map = {
            (s['row'], s['col'], s['spot_type'].id if hasattr(s['spot_type'], 'id') else s['spot_type']): s
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
            st_id = s.spot_type_id
            counters[st_id] = max(counters.get(st_id, 0), s.number)

        # Build and bulk create new spots, update existing spots if needed
        spots_to_create = []
        for coord, s in new_coords_map.items():
            if coord not in old_spots_map:
                # This is a new spot, assign next sequential number
                raw_st = s['spot_type']
                st_id = raw_st.id if hasattr(raw_st, 'id') else raw_st
                st = spot_types_map[st_id]
                
                counters[st.id] = counters.get(st.id, 0) + 1
                num = counters[st.id]
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
