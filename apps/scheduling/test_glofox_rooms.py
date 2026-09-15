from django.test import TestCase
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch

from apps.users.models import User, UserRole
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.scheduling.models import (
    Location, Room, SpotType, RoomLayout, Spot, ClassTemplate,
    ClassSession, Booking, PackageType, Package
)
from apps.scheduling.services import (
    create_layout, update_layout, validate_event_capacity, change_booking_spot, SpotUnavailableError
)
from apps.scheduling.tasks import auto_assign_unassigned


class GlofoxRoomCreationTestCase(TestCase):
    def setUp(self):
        # Create test tenant
        self.tenant = Tenant.objects.create(name="FitZone Studio", subdomain="fitzone")
        self.tenant_token = set_current_tenant(self.tenant)

        # Create test users
        self.owner = User.objects.create_user(
            email="owner@fitzone.com", password="password123", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.member1 = User.objects.create_user(
            email="member1@fitzone.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )
        self.member2 = User.objects.create_user(
            email="member2@fitzone.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )

        # Setup location
        self.location = Location.objects.create(
            tenant=self.tenant, name="Downtown Arena", address="100 Main St", timezone="UTC"
        )

        # Setup room
        self.room = Room.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Cycle Studio A",
            room_type="class",
            default_capacity=20
        )

        # Setup spot types
        self.bike_type = SpotType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Bike",
            prefix="B",
            is_bookable=True,
            color="#3B82F6"
        )
        self.instructor_type = SpotType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Instructor Stage",
            prefix="INST",
            is_bookable=False,
            color="#94A3B8"
        )

        # Setup package for bookings
        self.package_type = PackageType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="10-Ride Pack",
            credit_count=10,
            price=150.00,
            validity_days=30
        )
        self.pkg_member1 = Package.objects.create(
            tenant=self.tenant,
            client=self.member1,
            package_type=self.package_type,
            credits_remaining=5,
            expires_at=timezone.now() + timedelta(days=30)
        )
        self.pkg_member2 = Package.objects.create(
            tenant=self.tenant,
            client=self.member2,
            package_type=self.package_type,
            credits_remaining=5,
            expires_at=timezone.now() + timedelta(days=30)
        )

        # Setup ClassTemplate
        self.template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Power Spin",
            duration_min=45,
            default_capacity=20
        )

        self.client = APIClient()
        self.client.defaults['HTTP_HOST'] = f"{self.tenant.subdomain}.example.com"

    def test_room_soft_delete_and_restore(self):
        """Verify soft deletion and restoration of rooms."""
        room_id = self.room.id
        self.room.delete()
        self.assertTrue(self.room.is_deleted)
        self.assertIsNotNone(self.room.deleted_at)

        # Room not in alive queryset
        self.assertFalse(Room.objects.alive().filter(id=room_id).exists())
        # Room is in all_objects
        self.assertTrue(Room.all_objects.filter(id=room_id).exists())

        # Test restore via API
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(f"/api/v1/scheduling/rooms/{room_id}/restore/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.room.refresh_from_db()
        self.assertFalse(self.room.is_deleted)
        self.assertIsNone(self.room.deleted_at)
        self.assertTrue(Room.objects.alive().filter(id=room_id).exists())

    def test_spot_type_max_20_validation(self):
        """Verify maximum 20 spot types limit per location."""
        self.client.force_authenticate(user=self.owner)
        # We already have 2 spot types created in setUp
        for i in range(3, 21):
            SpotType.objects.create(
                tenant=self.tenant,
                location=self.location,
                name=f"SpotType {i}",
                prefix=f"ST{i}"
            )

        self.assertEqual(SpotType.objects.filter(location=self.location).count(), 20)

        # Creating the 21st spot type via API must be rejected
        res = self.client.post("/api/v1/scheduling/spot-types/", {
            "location": str(self.location.id),
            "name": "Excess Spot Type",
            "prefix": "EX",
            "is_bookable": True
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Maximum of 20 spot types", str(res.data))

    def test_layout_atomic_creation_and_labels(self):
        """Verify 2D layout creation with auto-generated spot labels."""
        spots_payload = [
            {"row": 0, "col": 0, "spot_type": self.bike_type.id},
            {"row": 0, "col": 1, "spot_type": self.bike_type.id},
            {"row": 1, "col": 0, "spot_type": self.instructor_type.id},
        ]
        layout = create_layout(
            tenant=self.tenant,
            room=self.room,
            name="Standard Spin Grid",
            grid_rows=4,
            grid_cols=4,
            spots_data=spots_payload
        )
        self.assertEqual(layout.grid_rows, 4)
        self.assertEqual(layout.grid_cols, 4)
        self.assertEqual(layout.spots.count(), 3)
        # Derived capacity only counts bookable spots
        self.assertEqual(layout.capacity, 2)

        bike1 = layout.spots.get(row=0, col=0)
        bike2 = layout.spots.get(row=0, col=1)
        inst = layout.spots.get(row=1, col=0)

        self.assertEqual(bike1.label, "B1")
        self.assertEqual(bike2.label, "B2")
        self.assertEqual(inst.label, "INST1")

    def test_spot_map_endpoint_computed_states(self):
        """Verify GET /events/{id}/spot-map/ returns accurate server-computed states."""
        spots_payload = [
            {"row": 0, "col": 0, "spot_type": self.bike_type.id},
            {"row": 0, "col": 1, "spot_type": self.bike_type.id},
            {"row": 0, "col": 2, "spot_type": self.bike_type.id},
            {"row": 1, "col": 0, "spot_type": self.instructor_type.id},
        ]
        layout = create_layout(
            tenant=self.tenant,
            room=self.room,
            name="Map Test Layout",
            grid_rows=2,
            grid_cols=3,
            spots_data=spots_payload
        )
        spot_b1 = layout.spots.get(label="B1")
        spot_b2 = layout.spots.get(label="B2")
        spot_b3 = layout.spots.get(label="B3")

        start_time = timezone.now() + timedelta(days=1)
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            layout=layout,
            layout_version=layout.version,
            start_at=start_time,
            end_at=start_time + timedelta(minutes=45),
            capacity=10,
            blocked_spots=[str(spot_b3.id)]
        )

        # Member 1 books B1
        Booking.objects.create(
            tenant=self.tenant,
            client=self.member1,
            session=session,
            spot=spot_b1,
            status='booked'
        )

        # Request spot map as member 1
        self.client.force_authenticate(user=self.member1)
        res = self.client.get(f"/api/v1/scheduling/sessions/{session.id}/spot-map/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        data = res.data
        self.assertEqual(data['my_spot'], str(spot_b1.id))
        spots_by_id = {s['id']: s for s in data['spots']}

        self.assertEqual(spots_by_id[str(spot_b1.id)]['state'], 'mine')
        self.assertEqual(spots_by_id[str(spot_b2.id)]['state'], 'available')
        self.assertEqual(spots_by_id[str(spot_b3.id)]['state'], 'blocked')
        inst_spot = layout.spots.get(label="INST1")
        self.assertEqual(spots_by_id[str(inst_spot.id)]['state'], 'non_bookable')

    def test_spot_booking_and_anti_snipe_concurrency(self):
        """Verify exact spot booking and 409 conflict when spot is already taken."""
        spots_payload = [
            {"row": 0, "col": 0, "spot_type": self.bike_type.id},
            {"row": 0, "col": 1, "spot_type": self.bike_type.id},
        ]
        layout = create_layout(
            tenant=self.tenant, room=self.room, name="AntiSnipe Layout",
            grid_rows=2, grid_cols=2, spots_data=spots_payload
        )
        spot_b1 = layout.spots.get(label="B1")

        start_time = timezone.now() + timedelta(days=2)
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            layout=layout,
            layout_version=layout.version,
            start_at=start_time,
            end_at=start_time + timedelta(minutes=45),
            capacity=10
        )

        # Member 1 books spot B1
        self.client.force_authenticate(user=self.member1)
        res1 = self.client.post("/api/v1/scheduling/bookings/", {
            "session": str(session.id),
            "spot": str(spot_b1.id)
        })
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        b1 = Booking.objects.get(id=res1.data['id'])
        self.assertEqual(b1.spot_id, spot_b1.id)

        # Member 2 attempts to book the same spot B1 -> must receive 409 Conflict
        self.client.force_authenticate(user=self.member2)
        res2 = self.client.post("/api/v1/scheduling/bookings/", {
            "session": str(session.id),
            "spot": str(spot_b1.id)
        })
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res2.data.get('error'), 'spot_unavailable')

    def test_change_spot_before_class(self):
        """Verify member switching their reserved spot before class starts."""
        spots_payload = [
            {"row": 0, "col": 0, "spot_type": self.bike_type.id},
            {"row": 0, "col": 1, "spot_type": self.bike_type.id},
        ]
        layout = create_layout(
            tenant=self.tenant, room=self.room, name="ChangeSpot Layout",
            grid_rows=2, grid_cols=2, spots_data=spots_payload
        )
        spot_b1 = layout.spots.get(label="B1")
        spot_b2 = layout.spots.get(label="B2")

        start_time = timezone.now() + timedelta(days=3)
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            layout=layout,
            start_at=start_time,
            end_at=start_time + timedelta(minutes=45),
            capacity=10
        )

        booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.member1,
            session=session,
            spot=spot_b1,
            credit_source=self.pkg_member1,
            status='booked'
        )

        self.client.force_authenticate(user=self.member1)
        res = self.client.patch(f"/api/v1/scheduling/bookings/{booking.id}/spot/", {
            "spot_id": str(spot_b2.id)
        })
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        booking.refresh_from_db()
        self.assertEqual(booking.spot_id, spot_b2.id)

    def test_spot_blocking_and_unblocking(self):
        """Verify per-occurrence spot blocking via API."""
        spots_payload = [{"row": 0, "col": 0, "spot_type": self.bike_type.id}]
        layout = create_layout(
            tenant=self.tenant, room=self.room, name="Block Layout",
            grid_rows=2, grid_cols=2, spots_data=spots_payload
        )
        spot = layout.spots.first()
        start_time = timezone.now() + timedelta(days=1)
        session = ClassSession.objects.create(
            tenant=self.tenant, template=self.template, room=self.room,
            layout=layout, start_at=start_time, end_at=start_time + timedelta(minutes=45),
            capacity=5
        )

        self.client.force_authenticate(user=self.owner)
        block_res = self.client.post(f"/api/v1/scheduling/sessions/{session.id}/spots/block/", {
            "spot_id": str(spot.id)
        })
        self.assertEqual(block_res.status_code, status.HTTP_200_OK)
        session.refresh_from_db()
        self.assertIn(str(spot.id), session.blocked_spots)

        unblock_res = self.client.post(f"/api/v1/scheduling/sessions/{session.id}/spots/unblock/", {
            "spot_id": str(spot.id)
        })
        self.assertEqual(unblock_res.status_code, status.HTTP_200_OK)
        session.refresh_from_db()
        self.assertNotIn(str(spot.id), session.blocked_spots)

    def test_layout_version_bump_and_orphan_reassignment(self):
        """Verify layout editing bumps version and Celery reassigns displaced members."""
        spots_v1 = [
            {"row": 0, "col": 0, "spot_type": self.bike_type.id},
            {"row": 0, "col": 1, "spot_type": self.bike_type.id},
        ]
        layout = create_layout(
            tenant=self.tenant, room=self.room, name="Versioned Layout",
            grid_rows=2, grid_cols=2, spots_data=spots_v1
        )
        self.assertEqual(layout.version, 1)
        spot_b1 = layout.spots.get(row=0, col=0)
        spot_b2 = layout.spots.get(row=0, col=1)

        start_time = timezone.now() + timedelta(days=5)
        session = ClassSession.objects.create(
            tenant=self.tenant, template=self.template, room=self.room,
            layout=layout, layout_version=1, start_at=start_time, end_at=start_time + timedelta(minutes=45),
            capacity=5
        )

        booking = Booking.objects.create(
            tenant=self.tenant, client=self.member1, session=session, spot=spot_b1,
            credit_source=self.pkg_member1, status='booked'
        )

        # Operator edits layout removing B1 (row 0, col 0) and adding a new bike at row 1, col 1
        spots_v2 = [
            {"row": 0, "col": 1, "spot_type": self.bike_type.id},
            {"row": 1, "col": 1, "spot_type": self.bike_type.id},
        ]
        with patch('apps.scheduling.tasks.auto_assign_unassigned.delay') as mock_task:
            update_layout(layout=layout, spots_data=spots_v2)
            mock_task.assert_called_once_with(str(layout.id))

        layout.refresh_from_db()
        self.assertEqual(layout.version, 2)

        booking.refresh_from_db()
        self.assertEqual(booking.status, 'unassigned')
        self.assertIsNone(booking.spot)

        # Execute the Celery worker task directly
        auto_assign_unassigned(str(layout.id))
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'booked')
        self.assertIsNotNone(booking.spot)

    def test_optional_room_booking(self):
        """Verify sessions without room or layout operate cleanly as standard headcount bookings."""
        start_time = timezone.now() + timedelta(days=2)
        session_no_room = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            start_at=start_time,
            end_at=start_time + timedelta(minutes=45),
            capacity=10
        )
        self.client.force_authenticate(user=self.member1)
        res = self.client.post("/api/v1/scheduling/bookings/", {
            "session": str(session_no_room.id)
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        b = Booking.objects.get(id=res.data['id'])
        self.assertIsNone(b.spot)
        self.assertEqual(b.status, 'booked')
