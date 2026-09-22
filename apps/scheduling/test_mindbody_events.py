from datetime import timedelta
from unittest.mock import patch
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from apps.core.tenants.models import Tenant
from apps.users.models import User, UserRole
from apps.core.tenants.context import set_current_tenant
from .models import Location, Room, PackageType, Package, Event, EventSession, EventEnrollment


class MindbodyEventsTestCase(APITestCase):
    def setUp(self):
        self.patcher = patch('apps.scheduling.tasks.process_waitlist_promotion_job.delay')
        self.mock_waitlist_delay = self.patcher.start()
        self.addCleanup(self.patcher.stop)

        self.tenant = Tenant.objects.create(name="Mindbody Test Gym", subdomain="mb-test-gym")
        self.tenant_token = set_current_tenant(self.tenant)

        self.owner = User.objects.create_user(
            email="owner@mbtest.com", password="password123", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.trainer = User.objects.create_user(
            email="trainer@mbtest.com", password="password123", role=UserRole.TRAINER, tenant=self.tenant
        )
        self.client1 = User.objects.create_user(
            email="client1@mbtest.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )
        self.client2 = User.objects.create_user(
            email="client2@mbtest.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )
        self.client_no_pass = User.objects.create_user(
            email="nopass@mbtest.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )

        self.location = Location.objects.create(
            tenant=self.tenant, name="Downtown Center", address="100 Main St", timezone="UTC"
        )
        self.room = Room.objects.create(
            tenant=self.tenant, location=self.location, name="Workshop Hall", capacity=20
        )

        self.package_type = PackageType.objects.create(
            tenant=self.tenant, location=self.location, name="5-Class Pass", credit_count=5, price=50.00, validity_days=30
        )
        self.pkg1 = Package.objects.create(
            tenant=self.tenant, client=self.client1, package_type=self.package_type, credits_remaining=5,
            expires_at=timezone.now() + timedelta(days=30)
        )
        self.pkg2 = Package.objects.create(
            tenant=self.tenant, client=self.client2, package_type=self.package_type, credits_remaining=5,
            expires_at=timezone.now() + timedelta(days=30)
        )

    def test_owner_can_create_event_and_client_cannot(self):
        self.client.force_authenticate(user=self.owner)
        payload = {
            "title": "Kettlebell Masterclass",
            "description": "Intensive workshop on kettlebell biomechanics.",
            "category": "masterclass",
            "location": str(self.location.id),
            "room": str(self.room.id),
            "primary_instructor": str(self.trainer.id),
            "event_type": "single",
            "start_at": (timezone.now() + timedelta(days=5)).isoformat(),
            "end_at": (timezone.now() + timedelta(days=5, hours=3)).isoformat(),
            "capacity": 15,
            "waitlist_capacity": 5,
            "is_free": False,
            "credits_required": 2,
            "cancellation_cutoff_hours": 24
        }
        res = self.client.post('/api/v1/scheduling/workshops/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['title'], "Kettlebell Masterclass")
        self.assertEqual(res.data['credits_required'], 2)
        self.assertFalse(res.data['is_free'])

        # Client attempt to create should be 403 Forbidden
        self.client.force_authenticate(user=self.client1)
        res2 = self.client.post('/api/v1/scheduling/workshops/', payload, format='json')
        self.assertEqual(res2.status_code, status.HTTP_403_FORBIDDEN)

    def test_free_event_enrollment(self):
        """Client with NO passes can enroll in a free event."""
        free_event = Event.objects.create(
            tenant=self.tenant,
            location=self.location,
            room=self.room,
            primary_instructor=self.trainer,
            title="Community Nutrition Seminar",
            category="seminar",
            start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, hours=1),
            capacity=20,
            waitlist_capacity=5,
            is_free=True,
            credits_required=0,
            status='published'
        )

        self.client.force_authenticate(user=self.client_no_pass)
        res = self.client.post(f'/api/v1/scheduling/workshops/{free_event.id}/enroll/', {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['action'], 'registered')
        self.assertEqual(res.data['enrollment']['pricing_type'], 'free')
        self.assertEqual(res.data['enrollment']['credits_deducted'], 0)

        enrollment = EventEnrollment.objects.get(event=free_event, client=self.client_no_pass)
        self.assertEqual(enrollment.status, 'registered')
        self.assertIsNone(enrollment.credit_source)

    def test_paid_event_enrollment_with_active_pass(self):
        """Client enrolls in a paid workshop, deducting required credits from active pass."""
        paid_event = Event.objects.create(
            tenant=self.tenant,
            location=self.location,
            room=self.room,
            primary_instructor=self.trainer,
            title="Handstand & Inversions Workshop",
            category="workshop",
            start_at=timezone.now() + timedelta(days=3),
            end_at=timezone.now() + timedelta(days=3, hours=2),
            capacity=10,
            waitlist_capacity=5,
            is_free=False,
            credits_required=2,
            status='published'
        )

        initial_credits = self.pkg1.credits_remaining
        self.assertEqual(initial_credits, 5)

        self.client.force_authenticate(user=self.client1)
        res = self.client.post(f'/api/v1/scheduling/workshops/{paid_event.id}/enroll/', {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['action'], 'registered')
        self.assertEqual(res.data['enrollment']['pricing_type'], 'pass')
        self.assertEqual(res.data['enrollment']['credits_deducted'], 2)

        self.pkg1.refresh_from_db()
        self.assertEqual(self.pkg1.credits_remaining, 3)

        enrollment = EventEnrollment.objects.get(event=paid_event, client=self.client1)
        self.assertEqual(enrollment.credit_source, self.pkg1)
        self.assertEqual(enrollment.credits_deducted, 2)

    def test_paid_event_enrollment_rejected_without_pass(self):
        """Client without an active pass is rejected with HTTP 402 Payment Required."""
        paid_event = Event.objects.create(
            tenant=self.tenant,
            location=self.location,
            room=self.room,
            primary_instructor=self.trainer,
            title="Olympic Weightlifting Clinic",
            category="clinic",
            start_at=timezone.now() + timedelta(days=3),
            end_at=timezone.now() + timedelta(days=3, hours=2),
            capacity=10,
            waitlist_capacity=5,
            is_free=False,
            credits_required=1,
            status='published'
        )

        self.client.force_authenticate(user=self.client_no_pass)
        res = self.client.post(f'/api/v1/scheduling/workshops/{paid_event.id}/enroll/', {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertEqual(res.data['error'], 'payment_required')

    def test_duplicate_enrollment_rejected(self):
        free_event = Event.objects.create(
            tenant=self.tenant,
            location=self.location,
            room=self.room,
            primary_instructor=self.trainer,
            title="Mindfulness & Breathwork",
            category="workshop",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
            capacity=10,
            is_free=True,
            status='published'
        )

        self.client.force_authenticate(user=self.client1)
        res1 = self.client.post(f'/api/v1/scheduling/workshops/{free_event.id}/enroll/', {}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        res2 = self.client.post(f'/api/v1/scheduling/workshops/{free_event.id}/enroll/', {}, format='json')
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already enrolled", str(res2.data['detail']).lower())

    def test_waitlist_and_automatic_promotion_with_credit_refund(self):
        """
        Event capacity=1, waitlist_capacity=1.
        - Client 1 registers (credits deducted: 5 -> 4).
        - Client 2 waitlisted (credits untouched: 5).
        - Client 1 cancels before cutoff:
          - Client 1 refunded (credits restored: 4 -> 5).
          - Client 2 promoted to registered (credits deducted: 5 -> 4).
        """
        event = Event.objects.create(
            tenant=self.tenant,
            location=self.location,
            room=self.room,
            primary_instructor=self.trainer,
            title="Exclusive High-Performance Camp",
            category="retreat",
            start_at=timezone.now() + timedelta(days=7),
            end_at=timezone.now() + timedelta(days=7, hours=4),
            capacity=1,
            waitlist_capacity=1,
            is_free=False,
            credits_required=1,
            cancellation_cutoff_hours=24,
            status='published'
        )

        # 1. Client 1 enrolls
        self.client.force_authenticate(user=self.client1)
        res1 = self.client.post(f'/api/v1/scheduling/workshops/{event.id}/enroll/', {}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res1.data['action'], 'registered')
        self.pkg1.refresh_from_db()
        self.assertEqual(self.pkg1.credits_remaining, 4)

        # 2. Client 2 enrolls - goes to waitlist
        self.client.force_authenticate(user=self.client2)
        res2 = self.client.post(f'/api/v1/scheduling/workshops/{event.id}/enroll/', {}, format='json')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data['action'], 'waitlisted')
        self.pkg2.refresh_from_db()
        self.assertEqual(self.pkg2.credits_remaining, 5)  # NOT deducted yet!

        # 3. Third user attempts - capacity & waitlist full (409)
        self.client.force_authenticate(user=self.client_no_pass)
        res3 = self.client.post(f'/api/v1/scheduling/workshops/{event.id}/enroll/', {}, format='json')
        self.assertEqual(res3.status_code, status.HTTP_409_CONFLICT)

        # 4. Client 1 cancels enrollment early
        self.client.force_authenticate(user=self.client1)
        enr1 = EventEnrollment.objects.get(event=event, client=self.client1)
        cancel_res = self.client.post(f'/api/v1/scheduling/event-enrollments/{enr1.id}/cancel/', {"reason": "Schedule clash"}, format='json')
        self.assertEqual(cancel_res.status_code, status.HTTP_200_OK)

        # Check Client 1 got refunded
        self.pkg1.refresh_from_db()
        self.assertEqual(self.pkg1.credits_remaining, 5)

        # Check Client 2 was automatically promoted and pass was deducted!
        enr2 = EventEnrollment.objects.get(event=event, client=self.client2)
        self.assertEqual(enr2.status, 'registered')
        self.assertEqual(enr2.credits_deducted, 1)
        self.assertEqual(enr2.credit_source, self.pkg2)
        self.pkg2.refresh_from_db()
        self.assertEqual(self.pkg2.credits_remaining, 4)

    def test_late_cancellation_does_not_refund_credits(self):
        """Late cancellation (past cutoff) retains deducted credits."""
        event = Event.objects.create(
            tenant=self.tenant,
            location=self.location,
            room=self.room,
            primary_instructor=self.trainer,
            title="Late Cancellation Workshop",
            category="workshop",
            start_at=timezone.now() + timedelta(hours=6),
            end_at=timezone.now() + timedelta(hours=8),
            capacity=5,
            is_free=False,
            credits_required=1,
            cancellation_cutoff_hours=12,  # 12 hours cutoff, event in 6 hours
            status='published'
        )

        self.client.force_authenticate(user=self.client1)
        self.client.post(f'/api/v1/scheduling/workshops/{event.id}/enroll/', {}, format='json')
        self.pkg1.refresh_from_db()
        self.assertEqual(self.pkg1.credits_remaining, 4)

        enr = EventEnrollment.objects.get(event=event, client=self.client1)
        cancel_res = self.client.post(f'/api/v1/scheduling/event-enrollments/{enr.id}/cancel/', {"reason": "Can't make it"}, format='json')
        self.assertEqual(cancel_res.status_code, status.HTTP_200_OK)

        self.pkg1.refresh_from_db()
        self.assertEqual(self.pkg1.credits_remaining, 4)  # Not refunded due to late cutoff

    def test_staff_check_in_and_roster(self):
        event = Event.objects.create(
            tenant=self.tenant,
            location=self.location,
            room=self.room,
            primary_instructor=self.trainer,
            title="Pilates Workshop",
            category="workshop",
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=2),
            capacity=10,
            is_free=True,
            status='published'
        )

        enr = EventEnrollment.objects.create(
            tenant=self.tenant,
            event=event,
            client=self.client1,
            status='registered',
            pricing_type='free'
        )

        # Staff can view roster
        self.client.force_authenticate(user=self.trainer)
        roster_res = self.client.get(f'/api/v1/scheduling/workshops/{event.id}/roster/')
        self.assertEqual(roster_res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(roster_res.data['roster']), 1)
        self.assertEqual(roster_res.data['roster'][0]['client_email'], self.client1.email)

        # Staff checks in attendee
        checkin_res = self.client.post(
            f'/api/v1/scheduling/workshops/{event.id}/check_in/',
            {"client_id": str(self.client1.id)},
            format='json'
        )
        self.assertEqual(checkin_res.status_code, status.HTTP_200_OK)
        enr.refresh_from_db()
        self.assertEqual(enr.status, 'attended')
        self.assertIsNotNone(enr.checked_in_at)

        # Client cannot view roster (403)
        self.client.force_authenticate(user=self.client1)
        client_roster_res = self.client.get(f'/api/v1/scheduling/workshops/{event.id}/roster/')
        self.assertEqual(client_roster_res.status_code, status.HTTP_403_FORBIDDEN)

    def test_event_host_cancellation_refunds_all_attendees(self):
        event = Event.objects.create(
            tenant=self.tenant,
            location=self.location,
            room=self.room,
            primary_instructor=self.trainer,
            title="Cancelled Masterclass",
            category="masterclass",
            start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, hours=3),
            capacity=5,
            is_free=False,
            credits_required=2,
            status='published'
        )

        # Enroll client 1
        self.client.force_authenticate(user=self.client1)
        self.client.post(f'/api/v1/scheduling/workshops/{event.id}/enroll/', {}, format='json')
        self.pkg1.refresh_from_db()
        self.assertEqual(self.pkg1.credits_remaining, 3)

        # Enroll client 2
        self.client.force_authenticate(user=self.client2)
        self.client.post(f'/api/v1/scheduling/workshops/{event.id}/enroll/', {}, format='json')
        self.pkg2.refresh_from_db()
        self.assertEqual(self.pkg2.credits_remaining, 3)

        # Host (owner) cancels the event
        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            f'/api/v1/scheduling/workshops/{event.id}/cancel_event/',
            {"reason": "Instructor emergency"},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Verify event is cancelled and both clients are refunded
        event.refresh_from_db()
        self.assertEqual(event.status, 'cancelled')

        self.pkg1.refresh_from_db()
        self.assertEqual(self.pkg1.credits_remaining, 5)

        self.pkg2.refresh_from_db()
        self.assertEqual(self.pkg2.credits_remaining, 5)
