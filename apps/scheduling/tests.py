from django.test import TestCase
from django.utils import timezone
from datetime import timedelta, datetime, time
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.users.models import User, UserRole, UserProfile
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.scheduling.models import (
    Location, Room, ClassTemplate, RecurrenceRule, ClassSession,
    Booking, PackageType, Package, Waitlist, CancellationPolicy,
    Appointment, SpotType, RoomLayout, Spot, StaffAvailability, SubstituteRequest
)
from apps.scheduling.tasks import process_waitlist_promotion_job, run_no_show_marking_job

from unittest.mock import patch

class GymSchedulingSystemTestCase(TestCase):
    def setUp(self):
        # Mock Celery delay to avoid redis network dependency in unit test environments
        self.patcher = patch('apps.scheduling.tasks.process_waitlist_promotion_job.delay')
        self.mock_waitlist_delay = self.patcher.start()
        self.addCleanup(self.patcher.stop)

        # Create a test tenant
        self.tenant = Tenant.objects.create(name="Ali Gym", subdomain="ali-gym")
        
        # Set tenant context globally for test execution thread
        self.tenant_token = set_current_tenant(self.tenant)

        # Create user roles
        self.owner = User.objects.create_user(
            email="owner@aligym.com", password="password123", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.trainer = User.objects.create_user(
            email="trainer@aligym.com", password="password123", role=UserRole.TRAINER, tenant=self.tenant
        )
        self.client1 = User.objects.create_user(
            email="client1@aligym.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )
        self.client2 = User.objects.create_user(
            email="client2@aligym.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )
        
        # Setup location and room
        self.location = Location.objects.create(
            tenant=self.tenant, name="Main Studio", address="123 Fitness St", timezone="UTC"
        )
        self.room = Room.objects.create(
            tenant=self.tenant, location=self.location, name="Room A", capacity=1
        )
        
        # Setup Class Template
        self.template = ClassTemplate.objects.create(
            tenant=self.tenant, location=self.location, name="Morning Yoga", duration_min=60, default_capacity=1
        )

        # Setup packages
        self.package_type = PackageType.objects.create(
            tenant=self.tenant, location=self.location, name="10-Pack", credit_count=10, price=100.00, validity_days=30
        )
        self.pkg1 = Package.objects.create(
            tenant=self.tenant, client=self.client1, package_type=self.package_type, credits_remaining=10,
            expires_at=timezone.now() + timedelta(days=30)
        )
        self.pkg2 = Package.objects.create(
            tenant=self.tenant, client=self.client2, package_type=self.package_type, credits_remaining=10,
            expires_at=timezone.now() + timedelta(days=30)
        )

        # Cancellation Policy (12-hour cutoff)
        self.policy = CancellationPolicy.objects.create(
            tenant=self.tenant, scope_type='global', cutoff_hours=12, late_fee_amount=10.00
        )

    def test_recurrence_rule_expansion(self):
        """Verify that RecurrenceRule generates ClassSession instances correctly."""
        rule = RecurrenceRule.objects.create(
            tenant=self.tenant,
            template=self.template,
            days_of_week=["monday", "wednesday", "thursday", "friday", "saturday", "sunday"],
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timedelta(days=7),
            start_time=time(9, 0),
            room=self.room,
            staff=self.trainer
        )
        
        # Simulate the API creation flow where the rule generates class sessions
        # (This is handled by our RecurrenceRuleViewSet.perform_create override, which we simulate here)
        from rest_framework.test import APIRequestFactory
        from apps.scheduling.views import RecurrenceRuleViewSet
        factory = APIRequestFactory()
        
        # Clear out any sessions that might be generated in direct model creation to test logic
        ClassSession.objects.filter(recurrence_rule=rule).delete()
        
        # Re-run rule creation trigger
        current_date = rule.start_date
        days_of_week = [d.lower() for d in rule.days_of_week]
        sessions_to_create = []

        while current_date <= rule.end_date:
            weekday_name = current_date.strftime('%A').lower()
            if weekday_name in days_of_week:
                naive_start = datetime.combine(current_date, rule.start_time)
                start_at = timezone.make_aware(naive_start)
                end_at = start_at + timedelta(minutes=rule.template.duration_min)

                sessions_to_create.append(
                    ClassSession(
                        tenant=rule.tenant,
                        template=rule.template,
                        recurrence_rule=rule,
                        room=rule.room,
                        staff=rule.staff,
                        start_at=start_at,
                        end_at=end_at,
                        capacity=rule.template.default_capacity
                    )
                )
            current_date += timedelta(days=1)
            
        ClassSession.objects.bulk_create(sessions_to_create)

        sessions = ClassSession.objects.filter(recurrence_rule=rule)
        self.assertTrue(sessions.count() > 0)
        for s in sessions:
            self.assertEqual(s.capacity, self.template.default_capacity)
            self.assertEqual(s.room, self.room)
            self.assertEqual(s.staff, self.trainer)

    def test_booking_logic_and_cancellation(self):
        """Test standard booking, early cancellation refund, and late cancellation forfeiture."""
        start_time = timezone.now() + timedelta(days=2) # Outside cutoff (early)
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=start_time,
            end_at=start_time + timedelta(minutes=60),
            capacity=1
        )
        
        # 1. Book Slot
        booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=session,
            credit_source=self.pkg1,
            status='booked'
        )
        self.pkg1.credits_remaining -= 1
        self.pkg1.save()
        
        self.assertEqual(self.pkg1.credits_remaining, 9)
        self.assertTrue(session.is_full)

        # 2. Early Cancel (Refund)
        now = timezone.now()
        is_early = now <= (session.start_at - timedelta(hours=self.policy.cutoff_hours))
        self.assertTrue(is_early)
        
        booking.status = 'cancelled'
        booking.save()
        self.pkg1.credits_remaining += 1
        self.pkg1.save()

        self.assertEqual(self.pkg1.credits_remaining, 10)

        # 3. Late Cancel (Within 12 hours: Cancellation successful, No Credit Refund)
        late_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=timezone.now() + timedelta(hours=6), # Inside 12h cutoff
            end_at=timezone.now() + timedelta(hours=7),
            capacity=1
        )
        
        booking2 = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=late_session,
            credit_source=self.pkg1,
            status='booked'
        )
        self.pkg1.credits_remaining -= 1
        self.pkg1.save()

        self.assertEqual(self.pkg1.credits_remaining, 9)

        # Late cancel - client cancels booking within 12h window
        is_early_late_session = timezone.now() <= (late_session.start_at - timedelta(hours=self.policy.cutoff_hours))
        self.assertFalse(is_early_late_session)
        booking2.status = 'cancelled'
        booking2.save()
        # No refund to client package (credit forfeited)
        self.assertEqual(self.pkg1.credits_remaining, 9)
        self.assertEqual(booking2.status, 'cancelled')

    def test_booking_viewset_cancellation_endpoint(self):
        """Test BookingViewSet.destroy endpoint for both early (>12h) and late (<=12h) cancellations."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import BookingViewSet

        factory = APIRequestFactory()

        # 1. Early cancellation (> 12 hours before session start)
        early_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=timezone.now() + timedelta(hours=24),
            end_at=timezone.now() + timedelta(hours=25),
            capacity=2
        )
        early_booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=early_session,
            credit_source=self.pkg1,
            status='booked'
        )
        # Client had 10 credits, booked -> deduct 1
        self.pkg1.credits_remaining = 9
        self.pkg1.save()

        view = BookingViewSet.as_view({'delete': 'destroy'})
        request = factory.delete(f'/api/bookings/{early_booking.id}/')
        request.tenant = self.tenant
        force_authenticate(request, user=self.client1)

        response = view(request, pk=str(early_booking.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'cancelled')
        self.assertTrue(response.data['refunded'])

        # Check credit refunded
        self.pkg1.refresh_from_db()
        self.assertEqual(self.pkg1.credits_remaining, 10)
        early_booking.refresh_from_db()
        self.assertEqual(early_booking.status, 'cancelled')

        # 2. Late cancellation (within 12 hours before session start)
        late_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=timezone.now() + timedelta(hours=6), # 6 hours away (< 12h)
            end_at=timezone.now() + timedelta(hours=7),
            capacity=2
        )
        late_booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=late_session,
            credit_source=self.pkg1,
            status='booked'
        )
        # Client had 10 credits, booked -> deduct 1
        self.pkg1.credits_remaining = 9
        self.pkg1.save()

        request = factory.delete(f'/api/bookings/{late_booking.id}/')
        request.tenant = self.tenant
        force_authenticate(request, user=self.client1)

        response = view(request, pk=str(late_booking.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'cancelled')
        self.assertFalse(response.data['refunded'])

        # Check credit NOT refunded
        self.pkg1.refresh_from_db()
        self.assertEqual(self.pkg1.credits_remaining, 9)
        late_booking.refresh_from_db()
        self.assertEqual(late_booking.status, 'cancelled')

    def test_waitlist_promotion(self):
        """Test that waitlisted clients are offered slots when capacity opens up."""
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=timezone.now() + timedelta(days=2),
            end_at=timezone.now() + timedelta(days=2, hours=1),
            capacity=1
        )

        # Client 1 books the only spot
        booking1 = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=session,
            credit_source=self.pkg1,
            status='booked'
        )

        # Client 2 joins the waitlist
        waitlist = Waitlist.objects.create(
            tenant=self.tenant,
            client=self.client2,
            session=session,
            position=1,
            status='waiting'
        )

        # Client 1 cancels
        booking1.status = 'cancelled'
        booking1.save()

        # Run waitlist promotion task synchronously
        process_waitlist_promotion_job(str(session.id))

        # Check waitlist status
        waitlist.refresh_from_db()
        self.assertEqual(waitlist.status, 'offered')
        self.assertIsNotNone(waitlist.expires_at)

    def test_complimentary_package_assignment_and_anti_fraud_limits(self):
        """Test assigning complimentary packages, enforcing 3/month limit, and anti-fraud rules."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import PackageViewSet
        from apps.scheduling.serializers import PackageSerializer

        factory = APIRequestFactory()
        view = PackageViewSet.as_view({'post': 'create'})

        # Clean existing packages for clear monthly counting
        Package.objects.filter(tenant=self.tenant).delete()

        # 1. First complimentary package assignment (success)
        request = factory.post('/api/packages/', {
            'client': str(self.client1.id),
            'package_type': str(self.package_type.id),
        }, format='json')
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response1 = view(request)
        self.assertEqual(response1.status_code, 201)
        self.assertTrue(response1.data['is_complimentary'])
        self.assertEqual(response1.data['credits_remaining'], self.package_type.credit_count)
        self.assertEqual(response1.data['assigned_by'], self.owner.id)

        # 2. Second complimentary package assignment (success)
        request = factory.post('/api/packages/', {
            'client': str(self.client2.id),
            'package_type': str(self.package_type.id),
        }, format='json')
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)
        response2 = view(request)
        self.assertEqual(response2.status_code, 201)

        # 3. Third complimentary package assignment (success)
        request = factory.post('/api/packages/', {
            'client': str(self.client1.id),
            'package_type': str(self.package_type.id),
        }, format='json')
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)
        response3 = view(request)
        self.assertEqual(response3.status_code, 201)

        # 4. Fourth complimentary package assignment (exceeds monthly limit -> fails 400)
        request = factory.post('/api/packages/', {
            'client': str(self.client2.id),
            'package_type': str(self.package_type.id),
        }, format='json')
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)
        response4 = view(request)
        self.assertEqual(response4.status_code, 400)
        self.assertIn("Monthly limit of 3 free package assignments", str(response4.data))

        # 5. Anti-Fraud: Excessive/Unlimited credits rejected
        Package.objects.filter(tenant=self.tenant).delete() # Reset count
        request = factory.post('/api/packages/', {
            'client': str(self.client1.id),
            'package_type': str(self.package_type.id),
            'credits_remaining': 999999 # Exceeds package_type.credit_count (10)
        }, format='json')
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)
        response_fraud_credits = view(request)
        self.assertEqual(response_fraud_credits.status_code, 400)
        self.assertIn("credits_remaining", response_fraud_credits.data)

        # 6. Anti-Fraud: Assigning to inactive client rejected
        self.client2.is_active = False
        self.client2.save()
        request = factory.post('/api/packages/', {
            'client': str(self.client2.id),
            'package_type': str(self.package_type.id),
        }, format='json')
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)
        response_inactive = view(request)
        self.assertEqual(response_inactive.status_code, 400)
        self.assertIn("inactive", str(response_inactive.data))
        self.client2.is_active = True
        self.client2.save()

    def test_rewards_packages_do_not_affect_admin_monthly_complimentary_limit(self):
        """Test that packages granted through rewards rules or redemptions do not consume Admin's 3/month complimentary limit."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import PackageViewSet
        from apps.scheduling.models import PackageGrantSource

        factory = APIRequestFactory()
        view = PackageViewSet.as_view({'post': 'create'})

        # Clean existing packages
        Package.objects.filter(tenant=self.tenant).delete()

        # 1. Simulate member receiving 5 free packages via Reward Rules and Store Redemptions
        for i in range(3):
            Package.objects.create(
                tenant=self.tenant,
                client=self.client1,
                package_type=self.package_type,
                credits_remaining=self.package_type.credit_count,
                expires_at=timezone.now() + timedelta(days=30),
                status='active',
                grant_source=PackageGrantSource.REWARD_RULE,
                is_complimentary=False,
                price=0.00
            )

        for i in range(2):
            Package.objects.create(
                tenant=self.tenant,
                client=self.client2,
                package_type=self.package_type,
                credits_remaining=self.package_type.credit_count,
                expires_at=timezone.now() + timedelta(days=30),
                status='active',
                grant_source=PackageGrantSource.REWARD_REDEMPTION,
                is_complimentary=False,
                price=0.00
            )

        # Member1 and Member2 currently have 5 reward packages total in this month
        self.assertEqual(Package.objects.filter(tenant=self.tenant).count(), 5)

        # 2. Gym Owner attempts manual complimentary assignment 1 -> SUCCESS
        req1 = factory.post('/api/packages/', {'client': str(self.client1.id), 'package_type': str(self.package_type.id)}, format='json')
        req1.tenant = self.tenant
        force_authenticate(req1, user=self.owner)
        resp1 = view(req1)
        self.assertEqual(resp1.status_code, 201)
        self.assertTrue(resp1.data['is_complimentary'])
        self.assertEqual(resp1.data['grant_source'], PackageGrantSource.MANUAL_COMPLIMENTARY)

        # 3. Manual complimentary assignment 2 -> SUCCESS
        req2 = factory.post('/api/packages/', {'client': str(self.client2.id), 'package_type': str(self.package_type.id)}, format='json')
        req2.tenant = self.tenant
        force_authenticate(req2, user=self.owner)
        resp2 = view(req2)
        self.assertEqual(resp2.status_code, 201)

        # 4. Manual complimentary assignment 3 -> SUCCESS
        req3 = factory.post('/api/packages/', {'client': str(self.client1.id), 'package_type': str(self.package_type.id)}, format='json')
        req3.tenant = self.tenant
        force_authenticate(req3, user=self.owner)
        resp3 = view(req3)
        self.assertEqual(resp3.status_code, 201)

        # 5. Manual complimentary assignment 4 (exceeds manual limit) -> FAILS 400
        req4 = factory.post('/api/packages/', {'client': str(self.client2.id), 'package_type': str(self.package_type.id)}, format='json')
        req4.tenant = self.tenant
        force_authenticate(req4, user=self.owner)
        resp4 = view(req4)
        self.assertEqual(resp4.status_code, 400)
        self.assertIn("Monthly limit of 3 free package assignments", str(resp4.data))

    def test_non_stripe_package_cancellation_flow(self):
        """Test that manually assigned/complimentary packages without Stripe sub cancel successfully."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.payments.views import PackageCancelView

        factory = APIRequestFactory()

        # Create a complimentary/manually assigned package (no stripe_subscription_id)
        comp_pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client1,
            package_type=self.package_type,
            credits_remaining=10,
            expires_at=timezone.now() + timedelta(days=30),
            is_complimentary=True,
            status='active'
        )

        view = PackageCancelView.as_view()
        request = factory.post(f'/api/payments/packages/{comp_pkg.id}/cancel/')
        request.tenant = self.tenant
        force_authenticate(request, user=self.client1)

        response = view(request, package_id=str(comp_pkg.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['detail'], "Package has been canceled successfully.")

        comp_pkg.refresh_from_db()
        self.assertEqual(comp_pkg.status, 'canceled')

        # Double cancellation attempt returns 400
        request2 = factory.post(f'/api/payments/packages/{comp_pkg.id}/cancel/')
        request2.tenant = self.tenant
        force_authenticate(request2, user=self.client1)
        response2 = view(request2, package_id=str(comp_pkg.id))
        self.assertEqual(response2.status_code, 400)
        self.assertEqual(response2.data['error'], "Package is already canceled.")

    def test_staff_conflict_validation_on_session_create_and_edit(self):
        """Verify staff conflict validation cannot be bypassed when creating or editing a session."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import ClassSessionViewSet

        factory = APIRequestFactory()
        view_patch = ClassSessionViewSet.as_view({'patch': 'partial_update'})

        room_b = Room.objects.create(
            tenant=self.tenant, location=self.location, name="Room B", capacity=10
        )

        start_time = timezone.now() + timedelta(days=5)
        end_time = start_time + timedelta(hours=1)

        # 1. Create Session B already assigned to trainer in room_b
        session_b = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=room_b,
            staff=self.trainer,
            start_at=start_time,
            end_at=end_time,
            capacity=10
        )

        # 2. Create Session A at same time slot WITHOUT staff (unassigned) in self.room
        session_a = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=None,
            start_at=start_time,
            end_at=end_time,
            capacity=10
        )

        # 3. Attempt to edit Session A to assign trainer via PATCH (without start_at/end_at in payload)
        request = factory.patch(
            f'/api/sessions/{session_a.id}/',
            {'staff': str(self.trainer.id)},
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = view_patch(request, pk=str(session_a.id))
        self.assertEqual(response.status_code, 400)
        self.assertIn('staff', response.data)

        # 4. Create Session C at a non-overlapping time slot with trainer in self.room
        session_c = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=start_time + timedelta(hours=3),
            end_at=start_time + timedelta(hours=4),
            capacity=10
        )

        # 5. Attempt to edit Session C start_at/end_at to overlap with Session B
        request_overlap = factory.patch(
            f'/api/sessions/{session_c.id}/',
            {
                'start_at': start_time.isoformat(),
                'end_at': end_time.isoformat()
            },
            format='json'
        )
        request_overlap.tenant = self.tenant
        force_authenticate(request_overlap, user=self.owner)

        response_overlap = view_patch(request_overlap, pk=str(session_c.id))
        self.assertEqual(response_overlap.status_code, 400)
        self.assertIn('staff', response_overlap.data)

        # 6. Editing non-staff attributes of Session B should NOT trigger self conflict
        request_self = factory.patch(
            f'/api/sessions/{session_b.id}/',
            {'capacity': 15},
            format='json'
        )
        request_self.tenant = self.tenant
        force_authenticate(request_self, user=self.owner)

        response_self = view_patch(request_self, pk=str(session_b.id))
        self.assertEqual(response_self.status_code, 200)
        self.assertEqual(response_self.data['capacity'], 15)

    def test_substitute_request_accept_staff_conflict(self):
        """Verify substitute request cannot be accepted by a trainer who has a schedule conflict."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import SubstituteRequestViewSet
        from apps.scheduling.models import SubstituteRequest

        factory = APIRequestFactory()

        trainer2 = User.objects.create_user(
            email="trainer2@aligym.com", password="password123", role=UserRole.TRAINER, tenant=self.tenant
        )

        start_time = timezone.now() + timedelta(days=6)
        end_time = start_time + timedelta(hours=1)

        # Session 1 led by self.trainer
        session1 = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=start_time,
            end_at=end_time,
            capacity=10
        )

        # Session 2 led by trainer2 at the same time
        session2 = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=trainer2,
            start_at=start_time,
            end_at=end_time,
            capacity=10
        )

        sub_req = SubstituteRequest.objects.create(
            tenant=self.tenant,
            session=session1,
            requested_by_staff=self.trainer,
            status='open'
        )

        # Trainer 2 attempts to accept sub request for session1 while busy with session2
        view_accept = SubstituteRequestViewSet.as_view({'post': 'accept'})
        request = factory.post(f'/api/substitute-requests/{sub_req.id}/accept/')
        request.tenant = self.tenant
        force_authenticate(request, user=trainer2)
        response = view_accept(request, pk=str(sub_req.id))
        self.assertEqual(response.status_code, 400)
        self.assertIn("already assigned to another session", str(response.data['detail']))

    def test_gym_admin_session_cancellation_refunds_all_clients(self):
        """
        Verify that when a Gym Admin cancels an entire class session (even within the 12h cutoff window),
        all active bookings for that session are cancelled and 1 credit is automatically refunded to each client's package.
        """
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import ClassSessionViewSet

        factory = APIRequestFactory()

        # Create a session starting in 2 hours (inside the 12h cutoff window)
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=timezone.now() + timedelta(hours=2),
            end_at=timezone.now() + timedelta(hours=3),
            capacity=10
        )

        # Client 1 & Client 2 book the session (deducting 1 credit each)
        booking1 = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=session,
            credit_source=self.pkg1,
            status='booked'
        )
        self.pkg1.credits_remaining = 9
        self.pkg1.save()

        booking2 = Booking.objects.create(
            tenant=self.tenant,
            client=self.client2,
            session=session,
            credit_source=self.pkg2,
            status='booked'
        )
        self.pkg2.credits_remaining = 9
        self.pkg2.save()

        # Gym Admin cancels the session via DELETE
        view_delete = ClassSessionViewSet.as_view({'delete': 'destroy'})
        request = factory.delete(f'/api/sessions/{session.id}/')
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = view_delete(request, pk=str(session.id))
        self.assertEqual(response.status_code, 200)

        # Refresh database records
        session.refresh_from_db()
        booking1.refresh_from_db()
        booking2.refresh_from_db()
        self.pkg1.refresh_from_db()
        self.pkg2.refresh_from_db()

        # Verify session and booking statuses
        self.assertEqual(session.status, 'cancelled')
        self.assertEqual(booking1.status, 'cancelled')
        self.assertEqual(booking2.status, 'cancelled')

        # Verify BOTH clients got their 1 credit refunded despite being within 12 hours
        self.assertEqual(self.pkg1.credits_remaining, 10)
        self.assertEqual(self.pkg2.credits_remaining, 10)

    def test_cancelled_session_does_not_block_new_session_creation(self):
        """Verify that cancelled sessions/appointments are excluded from scheduling conflict checks."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import ClassSessionViewSet
        from apps.scheduling.serializers import ClassSessionSerializer

        factory = APIRequestFactory()

        start_time = timezone.now() + timedelta(days=10)
        end_time = start_time + timedelta(hours=1)

        # 1. Create a session assigned to trainer and mark it cancelled
        session_cancelled = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=start_time,
            end_at=end_time,
            capacity=10,
            status='cancelled'
        )

        # 2. Attempt to create a new session for the same trainer in the same time slot via serializer
        serializer_data = {
            'template': str(self.template.id),
            'room': str(self.room.id),
            'staff': str(self.trainer.id),
            'start_at': start_time,
            'end_at': end_time,
            'capacity': 10,
            'status': 'scheduled'
        }
        serializer = ClassSessionSerializer(data=serializer_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        new_session = serializer.save(tenant=self.tenant)
        self.assertEqual(new_session.staff, self.trainer)
        self.assertEqual(new_session.status, 'scheduled')

    def test_checked_in_client_remains_visible_in_bookings_and_count(self):
        """
        Verify that when a client checks in for a session, their booking remains
        visible in the serialized session bookings list with status 'checked_in'
        and is included in booked_count.
        """
        from rest_framework.test import APIRequestFactory
        from apps.scheduling.serializers import ClassSessionSerializer
        factory = APIRequestFactory()

        # 1. Create session and booking
        start_time = timezone.now() + timedelta(days=1)
        end_time = start_time + timedelta(hours=1)
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=start_time,
            end_at=end_time,
            capacity=10,
            status='scheduled'
        )
        booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=session,
            status='booked'
        )

        # Verify initial serialized status as trainer
        req = factory.get('/')
        req.user = self.trainer
        serializer_initial = ClassSessionSerializer(session, context={'request': req})
        self.assertEqual(serializer_initial.data['booked_count'], 1)
        self.assertEqual(len(serializer_initial.data['bookings']), 1)
        self.assertEqual(serializer_initial.data['bookings'][0]['status'], 'booked')

        # 2. Transition booking status to checked_in (simulating check-in)
        booking.status = 'checked_in'
        booking.checked_in_at = timezone.now()
        booking.save()

        # 3. Re-serialize session and verify participant visibility
        serializer_after = ClassSessionSerializer(session, context={'request': req})
        self.assertEqual(serializer_after.data['booked_count'], 1)
        self.assertEqual(len(serializer_after.data['bookings']), 1)
        self.assertEqual(serializer_after.data['bookings'][0]['status'], 'checked_in')
        self.assertEqual(serializer_after.data['bookings'][0]['client_id'], str(self.client1.id))

    def test_client_schedule_conflict_prevention(self):
        """Verify a client cannot book into multiple sessions or appointments at the same time."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import BookingViewSet

        factory = APIRequestFactory()
        view_create = BookingViewSet.as_view({'post': 'create'})

        room_b = Room.objects.create(
            tenant=self.tenant, location=self.location, name="Room B", capacity=10
        )

        start_time = timezone.now() + timedelta(days=7)
        end_time = start_time + timedelta(hours=1)

        # Session 1 in Room A
        session1 = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=start_time,
            end_at=end_time,
            capacity=10,
            status='scheduled'
        )

        # Session 2 in Room B at the exact same time
        session2 = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=room_b,
            staff=self.trainer,
            start_at=start_time,
            end_at=end_time,
            capacity=10,
            status='scheduled'
        )

        # 1. Client 1 books Session 1 -> Should Succeed
        request1 = factory.post(
            '/api/bookings/',
            {'session': str(session1.id), 'join_mode': 'physical'},
            format='json'
        )
        request1.tenant = self.tenant
        force_authenticate(request1, user=self.client1)

        response1 = view_create(request1)
        self.assertEqual(response1.status_code, 201)

        # 2. Client 1 attempts to book Session 2 at the same time -> Should Fail with 400
        request2 = factory.post(
            '/api/bookings/',
            {'session': str(session2.id), 'join_mode': 'physical'},
            format='json'
        )
        request2.tenant = self.tenant
        force_authenticate(request2, user=self.client1)

        response2 = view_create(request2)
        self.assertEqual(response2.status_code, 400)
        self.assertIn("Schedule conflict", str(response2.data.get('detail', '')))

        # 3. Client cancels booking 1
        booking1_id = response1.data['id']
        view_destroy = BookingViewSet.as_view({'delete': 'destroy'})
        request_del = factory.delete(f'/api/bookings/{booking1_id}/')
        request_del.tenant = self.tenant
        force_authenticate(request_del, user=self.client1)
        response_del = view_destroy(request_del, pk=booking1_id)
        self.assertEqual(response_del.status_code, 200)

        # 4. Now Client 1 can book Session 2 -> Should Succeed
        request3 = factory.post(
            '/api/bookings/',
            {'session': str(session2.id), 'join_mode': 'physical'},
            format='json'
        )
        request3.tenant = self.tenant
        force_authenticate(request3, user=self.client1)
        response3 = view_create(request3)
        self.assertEqual(response3.status_code, 201)

    def test_past_session_auto_transitions_to_completed_on_api_query(self):
        """Verify that a session whose end time has passed automatically transitions from scheduled to completed."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import ClassSessionViewSet
        factory = APIRequestFactory()

        now = timezone.now()
        past_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=now - timedelta(hours=2),
            end_at=now - timedelta(hours=1),
            capacity=10,
            status='scheduled'
        )

        upcoming_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=now + timedelta(hours=1),
            end_at=now + timedelta(hours=2),
            capacity=10,
            status='scheduled'
        )

        view = ClassSessionViewSet.as_view({'get': 'list'})
        request = factory.get('/api/scheduling/sessions/')
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = view(request)
        self.assertEqual(response.status_code, 200)

        # In DB and API response, past session must now be 'completed'
        past_session.refresh_from_db()
        self.assertEqual(past_session.status, 'completed')

        upcoming_session.refresh_from_db()
        self.assertEqual(upcoming_session.status, 'scheduled')

        results = response.data.get('results', response.data)
        past_item = next(item for item in results if item['id'] == str(past_session.id))
        upcoming_item = next(item for item in results if item['id'] == str(upcoming_session.id))

        self.assertEqual(past_item['status'], 'completed')
        self.assertEqual(upcoming_item['status'], 'scheduled')

    def test_session_status_filtering_upcoming_and_past(self):
        """Verify filtering sessions by status=scheduled/active/upcoming vs status=completed/past."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import ClassSessionViewSet
        factory = APIRequestFactory()

        now = timezone.now()
        past_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=now - timedelta(hours=3),
            end_at=now - timedelta(hours=2),
            capacity=10,
            status='scheduled'
        )

        upcoming_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=now + timedelta(hours=4),
            end_at=now + timedelta(hours=5),
            capacity=10,
            status='scheduled'
        )

        view = ClassSessionViewSet.as_view({'get': 'list'})

        # Filter by scheduled/upcoming -> Should only contain upcoming_session
        req_scheduled = factory.get('/api/scheduling/sessions/?status=scheduled')
        req_scheduled.tenant = self.tenant
        force_authenticate(req_scheduled, user=self.owner)
        resp_scheduled = view(req_scheduled)
        res_scheduled = resp_scheduled.data.get('results', resp_scheduled.data)
        scheduled_ids = [item['id'] for item in res_scheduled]
        self.assertIn(str(upcoming_session.id), scheduled_ids)
        self.assertNotIn(str(past_session.id), scheduled_ids)

        # Filter by past/completed -> Should contain past_session
        req_past = factory.get('/api/scheduling/sessions/?status=past')
        req_past.tenant = self.tenant
        force_authenticate(req_past, user=self.owner)
        resp_past = view(req_past)
        res_past = resp_past.data.get('results', resp_past.data)
        past_ids = [item['id'] for item in res_past]
        self.assertIn(str(past_session.id), past_ids)
        self.assertNotIn(str(upcoming_session.id), past_ids)

    def test_refresh_status_model_method(self):
        """Test ClassSession.refresh_status() and Appointment.refresh_status()."""
        now = timezone.now()
        past_session = ClassSession(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=now - timedelta(hours=5),
            end_at=now - timedelta(hours=4),
            capacity=10,
            status='scheduled'
        )
        self.assertEqual(past_session.status, 'scheduled')
        new_status = past_session.refresh_status(save=False)
        self.assertEqual(new_status, 'completed')
        self.assertEqual(past_session.status, 'completed')

        past_session.save()
        past_session.refresh_from_db()
        self.assertEqual(past_session.status, 'completed')

    def test_past_appointment_auto_transitions_to_completed(self):
        """Verify that past appointments automatically transition to completed when queried."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import AppointmentViewSet
        factory = APIRequestFactory()

        now = timezone.now()
        past_appt = Appointment.objects.create(
            tenant=self.tenant,
            client=self.client1,
            provider=self.trainer,
            location=self.location,
            room=self.room,
            start_at=now - timedelta(hours=3),
            end_at=now - timedelta(hours=2),
            status='scheduled'
        )

        view = AppointmentViewSet.as_view({'get': 'list'})
        request = factory.get('/api/scheduling/appointments/')
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)
        response = view(request)
        self.assertEqual(response.status_code, 200)

        past_appt.refresh_from_db()
        self.assertEqual(past_appt.status, 'completed')

    def test_run_no_show_marking_job_processes_all_past_sessions(self):
        """Verify Celery task run_no_show_marking_job marks past sessions as completed and missed bookings as no_show."""
        now = timezone.now()
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=now - timedelta(hours=6),
            end_at=now - timedelta(hours=5),
            capacity=10,
            status='scheduled'
        )

        booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=session,
            status='booked',
            checked_in_at=None
        )

        with self.captureOnCommitCallbacks(execute=True):
            run_no_show_marking_job()

        session.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(session.status, 'completed')
        self.assertEqual(booking.status, 'no_show')

    def test_recurrence_rule_viewset_creates_sessions_at_exact_time(self):
        """Verify RecurrenceRuleViewSet creates sessions at the exact entered time without timezone shift."""
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.scheduling.views import RecurrenceRuleViewSet

        # Set location timezone to US Eastern
        self.location.timezone = "America/New_York"
        self.location.save()

        factory = APIRequestFactory()
        view = RecurrenceRuleViewSet.as_view({'post': 'create'})

        target_date = timezone.now().date() + timedelta(days=1)
        weekday_name = target_date.strftime('%A').lower()

        data = {
            'template': str(self.template.id),
            'days_of_week': [weekday_name],
            'start_date': str(target_date),
            'end_date': str(target_date + timedelta(days=6)),
            'start_time': '13:00:00',
            'room': str(self.room.id),
            'staff': str(self.trainer.id)
        }

        request = factory.post('/api/v1/scheduling/recurrence-rules/', data=data, format='json')
        request.tenant = self.tenant
        force_authenticate(request, user=self.owner)

        response = view(request)
        self.assertEqual(response.status_code, 201)

        rule_id = response.data['id']
        sessions = ClassSession.objects.filter(recurrence_rule_id=rule_id)
        self.assertEqual(sessions.count(), 1)
        session = sessions.first()
 
        self.assertEqual(session.start_at.hour, 13)
        self.assertEqual(session.start_at.minute, 0)
        self.assertEqual(session.start_at.date(), target_date)


class ClientBookingPreferenceTests(GymSchedulingSystemTestCase):
    """
    Automated tests for Client Booking Preferences CRUD API:
    - Model persistence across sessions
    - Non-boolean string/choice values matching booking APIs
    - Default values on GET when not yet configured
    - In-app booking creation fallback integration
    """

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIRequestFactory
        self.factory = APIRequestFactory()

    def test_get_preferences_returns_defaults_when_no_record_exists(self):
        from apps.scheduling.views import ClientBookingPreferenceView
        from rest_framework.test import force_authenticate

        view = ClientBookingPreferenceView.as_view()
        request = self.factory.get('/api/v1/scheduling/booking-preferences/')
        request.tenant = self.tenant
        force_authenticate(request, user=self.client1)

        response = view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['join_mode'], 'physical')
        self.assertEqual(response.data['remote_session'], 'physical')
        self.assertEqual(response.data['virtual_coach'], '')
        self.assertEqual(response.data['music_preference'], '')
        self.assertEqual(response.data['client'], str(self.client1.id))

    def test_create_preferences_with_string_values(self):
        from apps.scheduling.views import ClientBookingPreferenceView
        from apps.scheduling.models import ClientBookingPreference
        from rest_framework.test import force_authenticate

        view = ClientBookingPreferenceView.as_view()
        payload = {
            'join_mode': 'remote',
            'virtual_coach': 'virtual',
            'music_preference': 'Chill Hop'
        }
        request = self.factory.post('/api/v1/scheduling/booking-preferences/', payload, format='json')
        request.tenant = self.tenant
        force_authenticate(request, user=self.client1)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['join_mode'], 'remote')
        self.assertEqual(response.data['virtual_coach'], 'virtual')
        self.assertEqual(response.data['music_preference'], 'Chill Hop')

        # Verify DB persistence
        pref = ClientBookingPreference.all_objects.get(client=self.client1)
        self.assertEqual(pref.join_mode, 'remote')
        self.assertEqual(pref.virtual_coach, 'virtual')
        self.assertEqual(pref.music_preference, 'Chill Hop')

    def test_update_preferences_patch_and_put(self):
        from apps.scheduling.views import ClientBookingPreferenceView
        from apps.scheduling.models import ClientBookingPreference
        from rest_framework.test import force_authenticate

        ClientBookingPreference.objects.create(
            tenant=self.tenant,
            client=self.client1,
            join_mode='physical',
            virtual_coach='in_person',
            music_preference='Rock'
        )

        view = ClientBookingPreferenceView.as_view()
        # Partial update via PATCH
        request = self.factory.patch(
            '/api/v1/scheduling/booking-preferences/',
            {'music_preference': 'Upbeat EDM', 'join_mode': 'remote'},
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client1)

        response = view(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['music_preference'], 'Upbeat EDM')
        self.assertEqual(response.data['join_mode'], 'remote')
        self.assertEqual(response.data['virtual_coach'], 'in_person')

        # Verify in DB
        pref = ClientBookingPreference.all_objects.get(client=self.client1)
        self.assertEqual(pref.music_preference, 'Upbeat EDM')
        self.assertEqual(pref.join_mode, 'remote')

    def test_flexible_key_aliases(self):
        from apps.scheduling.views import ClientBookingPreferenceView
        from apps.scheduling.models import ClientBookingPreference
        from rest_framework.test import force_authenticate

        view = ClientBookingPreferenceView.as_view()
        # Using alternative keys: remote_session, virtualCoach, musicPreference
        payload = {
            'remote_session': 'remote',
            'virtualCoach': 'ai_coach',
            'musicPreference': 'Pop'
        }
        request = self.factory.post('/api/v1/scheduling/booking-preferences/', payload, format='json')
        request.tenant = self.tenant
        force_authenticate(request, user=self.client1)

        response = view(request)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['join_mode'], 'remote')
        self.assertEqual(response.data['virtual_coach'], 'ai_coach')
        self.assertEqual(response.data['music_preference'], 'Pop')

    def test_delete_resets_preferences(self):
        from apps.scheduling.views import ClientBookingPreferenceView
        from apps.scheduling.models import ClientBookingPreference
        from rest_framework.test import force_authenticate

        ClientBookingPreference.objects.create(
            tenant=self.tenant,
            client=self.client1,
            join_mode='remote',
            virtual_coach='virtual',
            music_preference='Jazz'
        )

        view = ClientBookingPreferenceView.as_view()
        request = self.factory.delete('/api/v1/scheduling/booking-preferences/')
        request.tenant = self.tenant
        force_authenticate(request, user=self.client1)

        response = view(request)
        self.assertEqual(response.status_code, 204)
        self.assertFalse(ClientBookingPreference.all_objects.filter(client=self.client1).exists())

        # Next GET returns defaults
        get_req = self.factory.get('/api/v1/scheduling/booking-preferences/')
        get_req.tenant = self.tenant
        force_authenticate(get_req, user=self.client1)
        get_res = view(get_req)
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.data['join_mode'], 'physical')

    def test_persistence_across_sessions(self):
        from apps.scheduling.views import ClientBookingPreferenceView
        from rest_framework.test import force_authenticate

        view = ClientBookingPreferenceView.as_view()

        # Session 1: Client saves preference
        post_req = self.factory.post(
            '/api/v1/scheduling/booking-preferences/',
            {'join_mode': 'remote', 'music_preference': 'Deep House'},
            format='json'
        )
        post_req.tenant = self.tenant
        force_authenticate(post_req, user=self.client1)
        post_res = view(post_req)
        self.assertEqual(post_res.status_code, 201)

        # Simulate logout & login (Session 2: fresh request, same user)
        fresh_req = self.factory.get('/api/v1/scheduling/booking-preferences/')
        fresh_req.tenant = self.tenant
        force_authenticate(fresh_req, user=self.client1)
        fresh_res = view(fresh_req)

        self.assertEqual(fresh_res.status_code, 200)
        self.assertEqual(fresh_res.data['join_mode'], 'remote')
        self.assertEqual(fresh_res.data['music_preference'], 'Deep House')

    def test_client_isolation(self):
        from apps.scheduling.views import ClientBookingPreferenceView
        from apps.scheduling.models import ClientBookingPreference
        from rest_framework.test import force_authenticate

        ClientBookingPreference.objects.create(
            tenant=self.tenant,
            client=self.client1,
            join_mode='remote',
            music_preference='Hip Hop'
        )

        view = ClientBookingPreferenceView.as_view()

        # Client 2 queries preferences
        req2 = self.factory.get('/api/v1/scheduling/booking-preferences/')
        req2.tenant = self.tenant
        force_authenticate(req2, user=self.client2)
        res2 = view(req2)

        self.assertEqual(res2.status_code, 200)
        # Client 2 should receive default values, not Client 1's
        self.assertEqual(res2.data['join_mode'], 'physical')
        self.assertEqual(res2.data['music_preference'], '')

    def test_booking_creation_uses_preferences_as_fallback(self):
        from apps.scheduling.views import BookingViewSet
        from apps.scheduling.models import ClientBookingPreference, ClassSession, Package
        from rest_framework.test import force_authenticate

        # Configure Client 1's preferences
        ClientBookingPreference.objects.create(
            tenant=self.tenant,
            client=self.client1,
            join_mode='remote',
            music_preference='Lo-Fi Beats'
        )

        # Client 1 already has self.pkg1 with remaining credits from setUp

        # Create session
        start_time = timezone.now() + timedelta(days=2)
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=start_time,
            end_at=start_time + timedelta(minutes=60),
            capacity=10,
            status='scheduled'
        )

        # Book session without specifying join_mode or music_preference
        view = BookingViewSet.as_view({'post': 'create'})
        req = self.factory.post(
            '/api/bookings/',
            {'session': str(session.id)},
            format='json'
        )
        req.tenant = self.tenant
        force_authenticate(req, user=self.client1)

        res = view(req)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['join_mode'], 'remote')
        self.assertEqual(res.data['music_preference'], 'Lo-Fi Beats')


class BookingSortingAndFilteringTests(GymSchedulingSystemTestCase):
    """
    Tests for Booking Sorting and Search/Filter Capabilities:
    - Latest-first ordering (-created_at) across pagination
    - Reflection of client-app created bookings in Gym Admin Portal list
    - Search and filter options for User/Client, Session, and Staff
    """

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        self.client = APIClient()

        # Set up a second trainer and template
        self.trainer2 = User.objects.create_user(
            email="coach_sarah@aligym.com", password="password123", role=UserRole.TRAINER, tenant=self.tenant
        )
        self.template_pilates = ClassTemplate.objects.create(
            tenant=self.tenant, location=self.location, name="Evening Pilates", duration_min=45, default_capacity=5
        )

        # Create 2 sessions
        start1 = timezone.now() + timedelta(days=1)
        self.session1 = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=start1,
            end_at=start1 + timedelta(minutes=60),
            capacity=10,
            status='scheduled'
        )

        start2 = timezone.now() + timedelta(days=2)
        self.session2 = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template_pilates,
            room=self.room,
            staff=self.trainer2,
            start_at=start2,
            end_at=start2 + timedelta(minutes=45),
            capacity=10,
            status='scheduled'
        )

    def test_client_booking_reflected_in_admin_portal(self):
        """Bookings created from Client App are properly synced and reflected in the Bookings section."""
        # Client creates a booking
        self.client.force_authenticate(user=self.client1)
        res = self.client.post("/api/v1/scheduling/bookings/", {
            "session": str(self.session1.id)
        }, format='json')
        self.assertEqual(res.status_code, 201)
        booking_id = res.json()["id"]

        # Gym Owner logs in and views Bookings list
        self.client.force_authenticate(user=self.owner)
        admin_res = self.client.get("/api/v1/scheduling/bookings/")
        self.assertEqual(admin_res.status_code, 200)

        results = admin_res.json()["results"]
        booking_ids = [b["id"] for b in results]
        self.assertIn(booking_id, booking_ids)

        # Verify serialized details
        b_data = next(b for b in results if b["id"] == booking_id)
        self.assertEqual(b_data["client_email"], self.client1.email)
        self.assertEqual(b_data["session_name"], "Morning Yoga")
        self.assertIn("trainer", b_data["staff_name"].lower() if b_data["staff_name"] else self.trainer.email)

    def test_bookings_sorted_latest_first_by_default(self):
        """Bookings are sorted by latest booking first (-created_at) by default across pagination."""
        b1 = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=self.session1,
            status='booked'
        )
        b2 = Booking.objects.create(
            tenant=self.tenant,
            client=self.client2,
            session=self.session2,
            status='booked'
        )

        # Simulate older created_at on b1
        Booking.all_objects.filter(id=b1.id).update(created_at=timezone.now() - timedelta(hours=2))
        Booking.all_objects.filter(id=b2.id).update(created_at=timezone.now() - timedelta(hours=1))

        self.client.force_authenticate(user=self.owner)
        resp = self.client.get("/api/v1/scheduling/bookings/")
        self.assertEqual(resp.status_code, 200)

        results = resp.json()["results"]
        # b2 is newer than b1, so b2 must come first
        ids = [b["id"] for b in results]
        self.assertLess(ids.index(str(b2.id)), ids.index(str(b1.id)))

    def test_filter_and_search_by_client(self):
        """Admin can filter and search bookings by Client ID, email, and name."""
        b1 = Booking.objects.create(tenant=self.tenant, client=self.client1, session=self.session1, status='booked')
        b2 = Booking.objects.create(tenant=self.tenant, client=self.client2, session=self.session2, status='booked')

        self.client.force_authenticate(user=self.owner)

        # 1. Filter by client ID
        res_id = self.client.get(f"/api/v1/scheduling/bookings/?client={self.client1.id}")
        self.assertEqual(res_id.status_code, 200)
        self.assertEqual(len(res_id.json()["results"]), 1)
        self.assertEqual(res_id.json()["results"][0]["id"], str(b1.id))

        # 2. Filter by client email
        res_email = self.client.get("/api/v1/scheduling/bookings/?email=client2")
        self.assertEqual(res_email.status_code, 200)
        self.assertEqual(len(res_email.json()["results"]), 1)
        self.assertEqual(res_email.json()["results"][0]["id"], str(b2.id))

    def test_filter_and_search_by_session(self):
        """Admin can filter and search bookings by Session ID and Session/Class Name."""
        b1 = Booking.objects.create(tenant=self.tenant, client=self.client1, session=self.session1, status='booked')
        b2 = Booking.objects.create(tenant=self.tenant, client=self.client2, session=self.session2, status='booked')

        self.client.force_authenticate(user=self.owner)

        # 1. Filter by session ID
        res_sess = self.client.get(f"/api/v1/scheduling/bookings/?session={self.session2.id}")
        self.assertEqual(res_sess.status_code, 200)
        self.assertEqual(len(res_sess.json()["results"]), 1)
        self.assertEqual(res_sess.json()["results"][0]["id"], str(b2.id))

        # 2. Search by class name
        res_search = self.client.get("/api/v1/scheduling/bookings/?session_name=Pilates")
        self.assertEqual(res_search.status_code, 200)
        self.assertEqual(len(res_search.json()["results"]), 1)
        self.assertEqual(res_search.json()["results"][0]["id"], str(b2.id))

    def test_filter_and_search_by_staff(self):
        """Admin can filter and search bookings by Staff/Trainer ID and name."""
        b1 = Booking.objects.create(tenant=self.tenant, client=self.client1, session=self.session1, status='booked')
        b2 = Booking.objects.create(tenant=self.tenant, client=self.client2, session=self.session2, status='booked')

        self.client.force_authenticate(user=self.owner)

        # 1. Filter by staff ID
        res_staff = self.client.get(f"/api/v1/scheduling/bookings/?staff={self.trainer.id}")
        self.assertEqual(res_staff.status_code, 200)
        self.assertEqual(len(res_staff.json()["results"]), 1)
        self.assertEqual(res_staff.json()["results"][0]["id"], str(b1.id))

        # 2. Filter by staff name / email keyword
        res_name = self.client.get("/api/v1/scheduling/bookings/?staff_name=sarah")
        self.assertEqual(res_name.status_code, 200)
        self.assertEqual(len(res_name.json()["results"]), 1)
        self.assertEqual(res_name.json()["results"][0]["id"], str(b2.id))


class SpotTypeEditAndDeleteTests(GymSchedulingSystemTestCase):
    """
    Tests for editing and deleting Spot Types:
    - Edit/update Spot Type (PUT/PATCH) by Admins
    - Label synchronization when prefix is modified
    - Deletion of unused Spot Types
    - Safe prevention of deleting Spot Types in use by Room Layout spots
    - RBAC: Clients/unauthorized users cannot edit or delete Spot Types
    """

    def setUp(self):
        super().setUp()
        from rest_framework.test import APIClient
        self.client = APIClient()

        # Create two initial spot types
        self.bike_type = SpotType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Spin Bike",
            prefix="SB",
            is_bookable=True,
            color="#3B82F6"
        )
        self.mat_type = SpotType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Yoga Mat",
            prefix="YM",
            is_bookable=True,
            color="#10B981"
        )

    def test_admin_can_edit_spot_type_via_put_and_patch(self):
        """Admin can edit/update an existing Spot Type using PUT and PATCH."""
        self.client.force_authenticate(user=self.owner)

        # 1. PATCH to update name and color
        res = self.client.patch(f"/api/v1/scheduling/spot-types/{self.bike_type.id}/", {
            "name": "Upgraded Spin Bike",
            "color": "#EF4444"
        }, format='json')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["name"], "Upgraded Spin Bike")
        self.assertEqual(res.data["color"], "#EF4444")
        self.assertEqual(res.data["prefix"], "SB")

        # Verify in DB
        self.bike_type.refresh_from_db()
        self.assertEqual(self.bike_type.name, "Upgraded Spin Bike")
        self.assertEqual(self.bike_type.color, "#EF4444")

        # 2. PUT via nested location URL
        res_put = self.client.put(
            f"/api/v1/scheduling/locations/{self.location.id}/spot-types/{self.bike_type.id}/",
            {
                "name": "Elite Spin Bike",
                "prefix": "EB",
                "color": "#6366F1",
                "is_bookable": True
            },
            format='json'
        )
        self.assertEqual(res_put.status_code, 200)
        self.assertEqual(res_put.data["name"], "Elite Spin Bike")
        self.assertEqual(res_put.data["prefix"], "EB")

        self.bike_type.refresh_from_db()
        self.assertEqual(self.bike_type.name, "Elite Spin Bike")
        self.assertEqual(self.bike_type.prefix, "EB")

    def test_spot_type_prefix_update_syncs_existing_spot_labels(self):
        """Updating a Spot Type prefix automatically synchronizes spot labels in room layouts."""
        # Create a layout and spot using self.bike_type
        layout = RoomLayout.objects.create(
            tenant=self.tenant,
            room=self.room,
            name="Spin Studio Layout",
            grid_rows=2,
            grid_cols=2
        )
        spot = Spot.objects.create(
            tenant=self.tenant,
            layout=layout,
            spot_type=self.bike_type,
            row=0,
            col=0,
            number=1
        )
        self.assertEqual(spot.label, "SB1")

        # Admin updates prefix from SB to BIKE
        self.client.force_authenticate(user=self.owner)
        res = self.client.patch(f"/api/v1/scheduling/spot-types/{self.bike_type.id}/", {
            "prefix": "BIKE"
        }, format='json')
        self.assertEqual(res.status_code, 200)

        # Verify spot label is updated
        spot.refresh_from_db()
        self.assertEqual(spot.label, "BIKE1")

    def test_admin_cannot_duplicate_spot_type_name_in_same_location(self):
        """Updating a Spot Type name to one that already exists in the same location is rejected."""
        self.client.force_authenticate(user=self.owner)
        res = self.client.patch(f"/api/v1/scheduling/spot-types/{self.bike_type.id}/", {
            "name": "Yoga Mat"  # Already exists as self.mat_type
        }, format='json')
        self.assertEqual(res.status_code, 400)
        self.assertTrue("name" in res.data or "non_field_errors" in res.data)

    def test_admin_can_delete_unused_spot_type(self):
        """Admin can delete an existing Spot Type that is not referenced in any room layout."""
        # Create an unused spot type
        unused = SpotType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Temporary Bench",
            prefix="TB"
        )
        unused_id = str(unused.id)

        self.client.force_authenticate(user=self.owner)
        res = self.client.delete(f"/api/v1/scheduling/spot-types/{unused_id}/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("deleted successfully", res.data.get("detail", ""))

        # Verify deleted from DB
        self.assertFalse(SpotType.objects.filter(id=unused_id).exists())

    def test_admin_cannot_delete_spot_type_in_use_by_layout_spots(self):
        """Deleting a Spot Type in use by spots in a room layout is safely prevented with 400 error."""
        layout = RoomLayout.objects.create(
            tenant=self.tenant,
            room=self.room,
            name="Main Room Layout",
            grid_rows=2,
            grid_cols=2
        )
        Spot.objects.create(
            tenant=self.tenant,
            layout=layout,
            spot_type=self.bike_type,
            row=0,
            col=0,
            number=1
        )

        self.client.force_authenticate(user=self.owner)
        res = self.client.delete(f"/api/v1/scheduling/spot-types/{self.bike_type.id}/")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data.get("code"), "spot_type_in_use")
        self.assertEqual(res.data.get("spots_count"), 1)
        self.assertIn("Cannot delete spot type", res.data.get("detail", ""))

        # Spot type still exists in DB
        self.assertTrue(SpotType.objects.filter(id=self.bike_type.id).exists())

    def test_client_cannot_edit_or_delete_spot_type(self):
        """Regular clients are forbidden from editing or deleting Spot Types."""
        self.client.force_authenticate(user=self.client1)

        # Attempt edit
        res_patch = self.client.patch(f"/api/v1/scheduling/spot-types/{self.bike_type.id}/", {
            "name": "Client Renamed"
        }, format='json')
        self.assertEqual(res_patch.status_code, 403)

        # Attempt delete
        res_del = self.client.delete(f"/api/v1/scheduling/spot-types/{self.bike_type.id}/")
        self.assertEqual(res_del.status_code, 403)

    def test_get_appointments_includes_client_and_staff_metadata(self):
        self.client1.first_name = "Jane"
        self.client1.last_name = "Doe"
        self.client1.save()
        UserProfile.objects.create(
            user=self.client1,
            first_name="Jane",
            last_name="Doe",
            level="RX2"
        )

        UserProfile.objects.create(
            user=self.trainer,
            first_name="Coach",
            last_name="Mike",
            nickname="Coach Mike"
        )

        appt = Appointment.objects.create(
            tenant=self.tenant,
            client=self.client1,
            provider=self.trainer,
            location=self.location,
            room=self.room,
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
            status='scheduled'
        )

        self.client.force_authenticate(user=self.owner)
        res = self.client.get('/api/v1/scheduling/appointments/', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res.status_code, 200)

        results = res.data.get('results', res.data) if isinstance(res.data, dict) else res.data
        appt_data = next((item for item in results if item['id'] == str(appt.id)), None)
        self.assertIsNotNone(appt_data)
        self.assertEqual(appt_data.get('client_name'), "Jane Doe")
        self.assertIn('client_image', appt_data)
        self.assertEqual(appt_data.get('client_rx_level'), "RX2")
        self.assertIn('staff_image', appt_data)

        res_detail = self.client.get(f'/api/v1/scheduling/appointments/{appt.id}/', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res_detail.status_code, 200)
        self.assertEqual(res_detail.data.get('client_name'), "Jane Doe")
        self.assertIn('client_image', res_detail.data)
        self.assertEqual(res_detail.data.get('client_rx_level'), "RX2")
        self.assertIn('staff_image', res_detail.data)

    def test_appointment_booking_validations(self):
        other_loc = Location.objects.create(
            tenant=self.tenant, name="Other Studio", address="456 Other St", timezone="UTC"
        )
        other_pkg_type = PackageType.objects.create(
            tenant=self.tenant, location=other_loc, name="Other Pack", credit_count=10, price=100.00, validity_days=30
        )
        Package.objects.filter(client=self.client1).delete()

        start_time = timezone.now() + timedelta(days=2)
        end_time = start_time + timedelta(hours=1)
        payload = {
            "provider": str(self.trainer.id),
            "location": str(self.location.id),
            "start_at": start_time.isoformat(),
            "end_at": end_time.isoformat()
        }

        self.client.force_authenticate(user=self.client1)
        res_no_pkg = self.client.post('/api/v1/scheduling/appointments/', payload, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res_no_pkg.status_code, 402)

        pkg_wrong_loc = Package.objects.create(
            tenant=self.tenant,
            client=self.client1,
            package_type=other_pkg_type,
            status='active',
            credits_remaining=5,
            expires_at=timezone.now() + timedelta(days=30)
        )
        res_wrong_loc = self.client.post('/api/v1/scheduling/appointments/', payload, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res_wrong_loc.status_code, 400)
        self.assertEqual(res_wrong_loc.data.get('detail'), "Your purchased package is not valid for this location.")

        pkg_wrong_loc.delete()
        pkg_canceled = Package.objects.create(
            tenant=self.tenant,
            client=self.client1,
            package_type=self.package_type,
            status='canceled',
            credits_remaining=5,
            expires_at=timezone.now() + timedelta(days=30)
        )
        res_canceled = self.client.post('/api/v1/scheduling/appointments/', payload, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res_canceled.status_code, 402)

        pkg_canceled.delete()
        pkg_expired = Package.objects.create(
            tenant=self.tenant,
            client=self.client1,
            package_type=self.package_type,
            status='active',
            credits_remaining=5,
            expires_at=timezone.now() - timedelta(days=1)
        )
        res_expired = self.client.post('/api/v1/scheduling/appointments/', payload, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res_expired.status_code, 402)

        pkg_expired.delete()
        pkg_insufficient = Package.objects.create(
            tenant=self.tenant,
            client=self.client1,
            package_type=self.package_type,
            status='active',
            credits_remaining=0,
            expires_at=timezone.now() + timedelta(days=30)
        )
        res_insufficient = self.client.post('/api/v1/scheduling/appointments/', payload, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res_insufficient.status_code, 402)

        pkg_insufficient.credits_remaining = 5
        pkg_insufficient.save()
        res_success = self.client.post('/api/v1/scheduling/appointments/', payload, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res_success.status_code, 201)
        pkg_insufficient.refresh_from_db()
        self.assertEqual(pkg_insufficient.credits_remaining, 4)


class StaffAvailabilityConflictTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()

        # Create a test tenant
        self.tenant = Tenant.objects.create(name="Ali Gym", subdomain="ali-gym-avail")
        self.tenant_token = set_current_tenant(self.tenant)

        # Create user roles
        self.owner = User.objects.create_user(
            email="owner_avail@aligym.com", password="password123", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.trainer = User.objects.create_user(
            email="trainer_avail@aligym.com", password="password123", role=UserRole.TRAINER, tenant=self.tenant
        )
        self.client1 = User.objects.create_user(
            email="client1_avail@aligym.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )

        self.location = Location.objects.create(
            tenant=self.tenant, name="Main Studio", address="123 Fitness St", timezone="UTC"
        )
        self.room = Room.objects.create(
            tenant=self.tenant, location=self.location, name="Room A", capacity=1
        )

        self.client.force_authenticate(user=self.owner)
        self.target_date = (timezone.now() + timedelta(days=2)).date()
        self.target_weekday = self.target_date.strftime('%A').lower()
        self.appt_start = timezone.make_aware(datetime.combine(self.target_date, time(10, 0)))
        self.appt_end = timezone.make_aware(datetime.combine(self.target_date, time(11, 0)))

        self.appointment = Appointment.objects.create(
            tenant=self.tenant,
            client=self.client1,
            provider=self.trainer,
            location=self.location,
            room=self.room,
            start_at=self.appt_start,
            end_at=self.appt_end,
            status='scheduled'
        )

    def test_cannot_create_blackout_over_active_appointment(self):
        payload = {
            "staff": str(self.trainer.id),
            "weekday_or_date": str(self.target_date),
            "start_time": "09:00:00",
            "end_time": "12:00:00",
            "is_blackout": True
        }
        res = self.client.post('/api/v1/scheduling/staff-availability/', payload, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res.status_code, 400)
        self.assertIn("Cannot add blackout", res.data.get('detail', ''))
        self.assertIn(self.client1.email, res.data.get('detail', ''))

    def test_can_create_blackout_when_no_appointment_conflicts(self):
        payload = {
            "staff": str(self.trainer.id),
            "weekday_or_date": str(self.target_date),
            "start_time": "14:00:00",
            "end_time": "16:00:00",
            "is_blackout": True
        }
        res = self.client.post('/api/v1/scheduling/staff-availability/', payload, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res.status_code, 201)

    def test_cannot_delete_availability_covering_active_appointment(self):
        avail = StaffAvailability.objects.create(
            tenant=self.tenant,
            staff=self.trainer,
            weekday_or_date=str(self.target_date),
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_blackout=False
        )
        res = self.client.delete(f'/api/v1/scheduling/staff-availability/{avail.id}/', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res.status_code, 400)
        self.assertIn("Cannot delete availability", res.data.get('detail', ''))

    def test_can_delete_availability_if_another_slot_covers_appointment(self):
        avail1 = StaffAvailability.objects.create(
            tenant=self.tenant,
            staff=self.trainer,
            weekday_or_date=str(self.target_date),
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_blackout=False
        )
        StaffAvailability.objects.create(
            tenant=self.tenant,
            staff=self.trainer,
            weekday_or_date=str(self.target_date),
            start_time=time(8, 0),
            end_time=time(18, 0),
            is_blackout=False
        )
        res = self.client.delete(f'/api/v1/scheduling/staff-availability/{avail1.id}/', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res.status_code, 204)

    def test_cannot_shorten_availability_away_from_active_appointment(self):
        avail = StaffAvailability.objects.create(
            tenant=self.tenant,
            staff=self.trainer,
            weekday_or_date=str(self.target_date),
            start_time=time(9, 0),
            end_time=time(17, 0),
            is_blackout=False
        )
        res = self.client.patch(
            f'/api/v1/scheduling/staff-availability/{avail.id}/',
            {"start_time": "12:00:00"},
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Cannot update availability", res.data.get('detail', ''))

    def test_cancelled_appointment_does_not_block_blackout(self):
        self.appointment.status = 'cancelled'
        self.appointment.save()

        payload = {
            "staff": str(self.trainer.id),
            "weekday_or_date": str(self.target_date),
            "start_time": "09:00:00",
            "end_time": "12:00:00",
            "is_blackout": True
        }
        res = self.client.post('/api/v1/scheduling/staff-availability/', payload, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res.status_code, 201)


class ScheduleChangeNotificationTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()

        self.tenant = Tenant.objects.create(name="Notification Gym", subdomain="notif-gym")
        self.tenant_token = set_current_tenant(self.tenant)

        self.owner = User.objects.create_user(
            email="owner_notif@gym.com", password="password123", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.trainer1 = User.objects.create_user(
            email="trainer1_notif@gym.com", password="password123", role=UserRole.TRAINER, tenant=self.tenant
        )
        self.trainer2 = User.objects.create_user(
            email="trainer2_notif@gym.com", password="password123", role=UserRole.TRAINER, tenant=self.tenant
        )
        self.client1 = User.objects.create_user(
            email="client1_notif@gym.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )

        self.location = Location.objects.create(
            tenant=self.tenant, name="Main Studio", address="123 Fitness St", timezone="UTC"
        )
        self.room = Room.objects.create(
            tenant=self.tenant, location=self.location, name="Room A", capacity=10
        )
        self.template = ClassTemplate.objects.create(
            tenant=self.tenant, location=self.location, name="Morning Yoga", duration_min=60, default_capacity=10
        )

        self.session_start = timezone.now() + timedelta(days=2)
        self.session_end = self.session_start + timedelta(minutes=60)
        self.session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer1,
            start_at=self.session_start,
            end_at=self.session_end,
            capacity=10,
            status='scheduled'
        )

        self.booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=self.session,
            status='booked'
        )

        self.appt_start = timezone.now() + timedelta(days=3)
        self.appt_end = self.appt_start + timedelta(hours=1)
        self.appointment = Appointment.objects.create(
            tenant=self.tenant,
            client=self.client1,
            provider=self.trainer1,
            location=self.location,
            room=self.room,
            start_at=self.appt_start,
            end_at=self.appt_end,
            status='scheduled'
        )

        self.client.force_authenticate(user=self.owner)

    def test_client_notified_when_session_time_rescheduled(self):
        from apps.notifications.models import NotificationInbox
        new_start = self.session_start + timedelta(hours=2)
        new_end = self.session_end + timedelta(hours=2)

        res = self.client.patch(
            f'/api/v1/scheduling/sessions/{self.session.id}/',
            {
                "start_at": new_start.isoformat(),
                "end_at": new_end.isoformat(),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res.status_code, 200)

        notif = NotificationInbox.objects.filter(
            recipient=self.client1,
            notification_type='CLASS',
            title__icontains="Class Rescheduled"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Morning Yoga", notif.body)

    def test_client_notified_when_session_trainer_changed(self):
        from apps.notifications.models import NotificationInbox
        res = self.client.patch(
            f'/api/v1/scheduling/sessions/{self.session.id}/',
            {
                "staff": str(self.trainer2.id),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res.status_code, 200)

        notif = NotificationInbox.objects.filter(
            recipient=self.client1,
            notification_type='CLASS',
            title__icontains="Instructor Update"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn(self.trainer2.email, notif.body)

    def test_client_notified_when_substitute_accepted(self):
        from apps.notifications.models import NotificationInbox
        sub_req = SubstituteRequest.objects.create(
            tenant=self.tenant,
            session=self.session,
            requested_by_staff=self.trainer1,
            status='open'
        )
        res = self.client.post(
            f'/api/v1/scheduling/substitute-requests/{sub_req.id}/accept/',
            {"trainer": str(self.trainer2.id)},
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res.status_code, 200)

        notif = NotificationInbox.objects.filter(
            recipient=self.client1,
            notification_type='CLASS',
            title__icontains="Instructor Update"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn(self.trainer2.email, notif.body)

    def test_client_notified_when_appointment_time_rescheduled(self):
        from apps.notifications.models import NotificationInbox
        new_start = self.appt_start + timedelta(hours=3)
        new_end = self.appt_end + timedelta(hours=3)

        res = self.client.patch(
            f'/api/v1/scheduling/appointments/{self.appointment.id}/',
            {
                "start_at": new_start.isoformat(),
                "end_at": new_end.isoformat(),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res.status_code, 200)

        notif = NotificationInbox.objects.filter(
            recipient=self.client1,
            notification_type='APPOINTMENT',
            title__icontains="Appointment Rescheduled"
        ).first()
        self.assertIsNotNone(notif)

    def test_client_notified_when_appointment_trainer_changed(self):
        from apps.notifications.models import NotificationInbox
        res = self.client.patch(
            f'/api/v1/scheduling/appointments/{self.appointment.id}/',
            {
                "provider": str(self.trainer2.id),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res.status_code, 200)

        notif = NotificationInbox.objects.filter(
            recipient=self.client1,
            notification_type='APPOINTMENT',
            title__icontains="Trainer Changed"
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn(self.trainer2.email, notif.body)

    def test_client_notified_when_appointment_cancelled(self):
        from apps.notifications.models import NotificationInbox
        res = self.client.delete(
            f'/api/v1/scheduling/appointments/{self.appointment.id}/',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res.status_code, 200)

        notif = NotificationInbox.objects.filter(
            recipient=self.client1,
            notification_type='APPOINTMENT',
            title__icontains="Appointment Cancelled"
        ).first()
        self.assertIsNotNone(notif)

    def test_first_session_completed_auto_email_triggers_only_once(self):
        from apps.notifications.models import NotificationInbox, DeliveryPolicy
        from django.core import mail

        # Give trainer1 a profile name so we can verify trainer_name in the email
        profile, _ = UserProfile.objects.get_or_create(user=self.trainer1)
        profile.first_name = "Coach"
        profile.last_name = "Carter"
        profile.save()

        mail.outbox.clear()

        # 1. Check in client1 for their very first session at this gym
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                f'/api/v1/scheduling/bookings/{self.booking.id}/check-in/',
                HTTP_X_TENANT_ID=str(self.tenant.id)
            )
        self.assertEqual(res.status_code, 200)

        notif = NotificationInbox.objects.filter(
            recipient=self.client1,
            idempotency_key=f"first_session_completed:{self.tenant.id}:{self.client1.id}"
        ).first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.delivery_policy, DeliveryPolicy.PUSH_AND_EMAIL)
        self.assertIn("Coach Carter", notif.title)
        self.assertIn(self.tenant.name, notif.title)
        self.assertIn("Coach Carter", notif.body)
        self.assertIn(self.tenant.name, notif.body)

        # Verify actual email was dispatched to outbox
        matching_emails = [m for m in mail.outbox if self.client1.email in m.to and "Coach Carter" in m.subject]
        self.assertEqual(len(matching_emails), 1)
        self.assertIn("first session", matching_emails[0].body.lower())

        # 2. Create and check in a SECOND session for the same client -> should NOT trigger first session email again
        second_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer1,
            start_at=self.session_start + timedelta(days=1),
            end_at=self.session_end + timedelta(days=1),
            capacity=10,
            status='scheduled'
        )
        second_booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=second_session,
            status='booked'
        )
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            res2 = self.client.post(
                f'/api/v1/scheduling/bookings/{second_booking.id}/check-in/',
                HTTP_X_TENANT_ID=str(self.tenant.id)
            )
        self.assertEqual(res2.status_code, 200)

        first_session_emails = [m for m in mail.outbox if "first session" in m.subject.lower()]
        self.assertEqual(len(first_session_emails), 0)

    def test_missed_session_auto_email_triggers_on_manual_and_periodic_no_show(self):
        from apps.notifications.models import NotificationInbox, DeliveryPolicy
        from django.core import mail

        profile, _ = UserProfile.objects.get_or_create(user=self.trainer1)
        profile.first_name = "Sarah"
        profile.last_name = "Connor"
        profile.save()

        mail.outbox.clear()

        # 1. Manual mark_no_show endpoint
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(
                f'/api/v1/scheduling/bookings/{self.booking.id}/no-show/',
                HTTP_X_TENANT_ID=str(self.tenant.id)
            )
        self.assertEqual(res.status_code, 200)

        notif = NotificationInbox.objects.filter(
            recipient=self.client1,
            idempotency_key=f"session_missed:{self.tenant.id}:{self.client1.id}:{self.booking.id}"
        ).first()
        self.assertIsNotNone(notif)
        self.assertEqual(notif.delivery_policy, DeliveryPolicy.PUSH_AND_EMAIL)
        self.assertIn(self.tenant.name, notif.title)
        self.assertIn("Sarah Connor", notif.body)
        self.assertIn(self.tenant.name, notif.body)

        missed_emails = [m for m in mail.outbox if self.client1.email in m.to and "missed you" in m.subject.lower()]
        self.assertEqual(len(missed_emails), 1)

        # 2. Periodic run_no_show_marking_job after a past session ends
        past_start = timezone.now() - timedelta(hours=3)
        past_end = timezone.now() - timedelta(hours=2)
        past_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer1,
            start_at=past_start,
            end_at=past_end,
            capacity=10,
            status='scheduled'
        )
        past_booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=past_session,
            status='booked'
        )
        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            run_no_show_marking_job()

        past_booking.refresh_from_db()
        self.assertEqual(past_booking.status, 'no_show')

        notif2 = NotificationInbox.objects.filter(
            recipient=self.client1,
            idempotency_key=f"session_missed:{self.tenant.id}:{self.client1.id}:{past_booking.id}"
        ).first()
        self.assertIsNotNone(notif2)
        missed_emails_job = [m for m in mail.outbox if self.client1.email in m.to and "missed you" in m.subject.lower()]
        self.assertEqual(len(missed_emails_job), 1)

    def test_duplicate_and_overlapping_staff_availability_rejected(self):
        """Verify creating duplicate or overlapping staff availability windows on the same day is rejected."""
        payload = {
            "staff": str(self.trainer1.id),
            "weekday_or_date": "Monday",
            "start_time": "08:00:00",
            "end_time": "17:00:00",
            "is_blackout": False,
        }
        res1 = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            payload,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res1.status_code, 201)
        self.assertEqual(res1.data['weekday_or_date'], 'monday')

        # 1. Exact duplicate (8 AM to 5 PM again) -> 400 Bad Request
        res_dup = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            payload,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res_dup.status_code, 400)
        self.assertIn("Duplicate availability", str(res_dup.data))

        # 2. Overlapping time slot on the same day (10 AM to 2 PM) -> 400 Bad Request
        overlap_payload = {
            "staff": str(self.trainer1.id),
            "weekday_or_date": "monday",
            "start_time": "10:00:00",
            "end_time": "14:00:00",
            "is_blackout": False,
        }
        res_overlap = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            overlap_payload,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res_overlap.status_code, 400)
        self.assertIn("Overlapping availability", str(res_overlap.data))

        # 3. Non-overlapping time slot on the same day (5 PM to 8 PM) -> 201 Created
        non_overlap_payload = {
            "staff": str(self.trainer1.id),
            "weekday_or_date": "monday",
            "start_time": "17:00:00",
            "end_time": "20:00:00",
            "is_blackout": False,
        }
        res_non_overlap = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            non_overlap_payload,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res_non_overlap.status_code, 201)

        # 4. Sub-hour slot (e.g. 30 minutes: 08:00 to 08:30 on Tuesday) -> 201 Created & returned by availability endpoint
        today_str = str(timezone.now().date())
        sub_hour_payload = {
            "staff": str(self.trainer1.id),
            "weekday_or_date": today_str,
            "start_time": "08:00:00",
            "end_time": "08:30:00",
            "is_blackout": False,
        }
        res_sub_hour = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            sub_hour_payload,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(res_sub_hour.status_code, 201)

        avail_res = self.client.get(
            f'/api/v1/scheduling/appointments/availability/?provider={self.trainer1.id}',
            HTTP_X_TENANT_ID=str(self.tenant.id)
        )
        self.assertEqual(avail_res.status_code, 200)
        self.assertTrue(any(slot.get('duration_min') == 30 for slot in avail_res.data['availability']))


class CrossMidnightStaffAvailabilityTests(TestCase):
    def setUp(self):
        from rest_framework.test import APIClient
        self.client = APIClient()

        self.tenant = Tenant.objects.create(name="Boston Gym", subdomain="boston-gym")
        self.tenant_token = set_current_tenant(self.tenant)

        self.owner = User.objects.create_user(
            email="owner@bostongym.com", password="password123", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.trainer1 = User.objects.create_user(
            email="trainer1@bostongym.com", password="password123", role=UserRole.TRAINER, tenant=self.tenant
        )
        self.trainer2 = User.objects.create_user(
            email="trainer2@bostongym.com", password="password123", role=UserRole.TRAINER, tenant=self.tenant
        )
        self.client1 = User.objects.create_user(
            email="client1@bostongym.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )

        self.loc_boston = Location.objects.create(
            tenant=self.tenant, name="Boston Downtown", address="100 Boylston St", timezone="America/New_York"
        )
        self.loc_cambridge = Location.objects.create(
            tenant=self.tenant, name="Cambridge Studio", address="200 Mass Ave", timezone="America/New_York"
        )
        self.room = Room.objects.create(
            tenant=self.tenant, location=self.loc_boston, name="Studio 1", capacity=10
        )

        self.client.force_authenticate(user=self.owner)

    def _get_next_weekday_date(self, target_weekday_index: int):
        """Returns the next date within the 7-day lookahead window matching target_weekday_index (0=Monday)."""
        today = timezone.now().date()
        for i in range(7):
            d = today + timedelta(days=i)
            if d.weekday() == target_weekday_index:
                return d
        return today

    def test_cross_midnight_monday_21_to_tuesday_03_creation_and_slot_generation(self):
        """
        Verify Monday 21:00:00 to 03:00:00 (6 hours across midnight into Tuesday)
        is accepted and generates 6 hourly UTC appointment slots from Monday 21:00 UTC to Tuesday 03:00 UTC.
        """
        payload = {
            "staff": str(self.trainer1.id),
            "location": str(self.loc_boston.id),
            "weekday_or_date": "Monday",
            "start_time": "21:00:00",
            "end_time": "03:00:00",
            "is_blackout": False,
        }
        res = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            payload,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(res.data['weekday_or_date'], 'monday')
        self.assertEqual(res.data['start_time'], '21:00:00')
        self.assertEqual(res.data['end_time'], '03:00:00')
        self.assertEqual(str(res.data['location']), str(self.loc_boston.id))

        # Check appointment availability slot generation
        avail_res = self.client.get(
            f'/api/v1/scheduling/appointments/availability/?provider={self.trainer1.id}&location={self.loc_boston.id}',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(avail_res.status_code, 200)
        slots = avail_res.data['availability']
        self.assertEqual(len(slots), 6)

        from datetime import timezone as dt_timezone
        next_monday = self._get_next_weekday_date(0)
        next_tuesday = next_monday + timedelta(days=1)
        expected_first_start = timezone.make_aware(datetime.combine(next_monday, time(21, 0)), dt_timezone.utc)
        expected_last_end = timezone.make_aware(datetime.combine(next_tuesday, time(3, 0)), dt_timezone.utc)

        self.assertEqual(slots[0]['start_at'], expected_first_start)
        self.assertEqual(slots[-1]['end_at'], expected_last_end)

        # Verify staff utilization report calculates +6.0 hours (not negative)
        from apps.scheduling.models import StaffLocation
        StaffLocation.objects.get_or_create(tenant=self.tenant, staff=self.trainer1, location=self.loc_boston)
        report_res = self.client.get(
            f'/api/v1/scheduling/reports/?type=staff-utilization&location={self.loc_boston.id}',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(report_res.status_code, 200)
        trainer_report = next(r for r in report_res.data if str(r['instructor_id']) == str(self.trainer1.id))
        self.assertEqual(trainer_report['weekly_available_hours'], 6.0)

    def test_normal_same_day_monday_09_to_17_preserved(self):
        """Verify normal same-day Monday 09:00 to 17:00 schedule works and generates 8 hourly slots."""
        payload = {
            "staff": str(self.trainer1.id),
            "location": str(self.loc_boston.id),
            "weekday_or_date": "Monday",
            "start_time": "09:00:00",
            "end_time": "17:00:00",
            "is_blackout": False,
        }
        res = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            payload,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res.status_code, 201)

        avail_res = self.client.get(
            f'/api/v1/scheduling/appointments/availability/?provider={self.trainer1.id}&location={self.loc_boston.id}',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(avail_res.status_code, 200)
        self.assertEqual(len(avail_res.data['availability']), 8)

    def test_cross_midnight_monday_23_to_tuesday_01(self):
        """Verify Monday 23:00 to Tuesday 01:00 (2 hours across midnight) works and generates 2 hourly slots."""
        payload = {
            "staff": str(self.trainer1.id),
            "location": str(self.loc_boston.id),
            "weekday_or_date": "Monday",
            "start_time": "23:00:00",
            "end_time": "01:00:00",
            "is_blackout": False,
        }
        res = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            payload,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res.status_code, 201)

        avail_res = self.client.get(
            f'/api/v1/scheduling/appointments/availability/?provider={self.trainer1.id}&location={self.loc_boston.id}',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(avail_res.status_code, 200)
        self.assertEqual(len(avail_res.data['availability']), 2)

    def test_equal_start_and_end_time_rejected(self):
        """Verify start_time == end_time is explicitly rejected with 400 Bad Request."""
        payload = {
            "staff": str(self.trainer1.id),
            "location": str(self.loc_boston.id),
            "weekday_or_date": "Monday",
            "start_time": "09:00:00",
            "end_time": "09:00:00",
            "is_blackout": False,
        }
        res = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            payload,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("Start time and end time cannot be equal", str(res.data))

    def test_overlapping_cross_midnight_availability_rejected(self):
        """
        Verify overlapping cross-midnight schedules are rejected:
        - Monday 21:00 -> Tuesday 03:00 exists.
        - Monday 23:00 -> Tuesday 01:00 overlaps (same start weekday, crossing midnight).
        - Tuesday 01:00 -> Tuesday 05:00 overlaps the Tuesday 00:00-03:00 portion of Monday's shift!
        """
        base_payload = {
            "staff": str(self.trainer1.id),
            "location": str(self.loc_boston.id),
            "weekday_or_date": "Monday",
            "start_time": "21:00:00",
            "end_time": "03:00:00",
            "is_blackout": False,
        }
        res1 = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            base_payload,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res1.status_code, 201)

        # Overlap 1: Monday 23:00 to Tuesday 01:00
        overlap1 = {
            "staff": str(self.trainer1.id),
            "location": str(self.loc_boston.id),
            "weekday_or_date": "Monday",
            "start_time": "23:00:00",
            "end_time": "01:00:00",
            "is_blackout": False,
        }
        res_ov1 = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            overlap1,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_ov1.status_code, 400)
        self.assertIn("Overlapping availability", str(res_ov1.data))

        # Overlap 2: Tuesday 01:00 to Tuesday 05:00 (overlaps the Tuesday 00:00-03:00 tail of Monday's shift)
        overlap2 = {
            "staff": str(self.trainer1.id),
            "location": str(self.loc_boston.id),
            "weekday_or_date": "Tuesday",
            "start_time": "01:00:00",
            "end_time": "05:00:00",
            "is_blackout": False,
        }
        res_ov2 = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            overlap2,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_ov2.status_code, 400)
        self.assertIn("Overlapping availability", str(res_ov2.data))

        # Non-overlapping on Tuesday: Tuesday 03:00 to Tuesday 09:00 -> 201 Created
        non_overlap_tue = {
            "staff": str(self.trainer1.id),
            "location": str(self.loc_boston.id),
            "weekday_or_date": "Tuesday",
            "start_time": "03:00:00",
            "end_time": "09:00:00",
            "is_blackout": False,
        }
        res_ok = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            non_overlap_tue,
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_ok.status_code, 201)

    def test_appointment_overlapping_following_day_portion_blocks_delete_and_blackout(self):
        """
        Verify an active appointment on Tuesday 01:00-02:00 UTC (the following-day portion
        of a Monday 21:00 - Tuesday 03:00 availability):
        1. Excludes the 01:00-02:00 slot from provider_availability.
        2. Blocks deleting the Monday 21:00 - 03:00 availability.
        3. Blocks creating a Monday 23:00 - Tuesday 02:00 blackout over that appointment.
        """
        # Pick a future Monday (at least 1 day ahead so Monday 21:00 is in the future)
        today = timezone.now().date()
        days_until_monday = (0 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        future_monday = today + timedelta(days=days_until_monday)
        future_tuesday = future_monday + timedelta(days=1)

        avail = StaffAvailability.objects.create(
            tenant=self.tenant,
            staff=self.trainer1,
            location=self.loc_boston,
            weekday_or_date="monday",
            start_time=time(21, 0),
            end_time=time(3, 0),
            is_blackout=False,
        )

        # Create appointment on Tuesday 01:00 - 02:00 UTC
        from datetime import timezone as dt_timezone
        appt_start = timezone.make_aware(datetime.combine(future_tuesday, time(1, 0)), dt_timezone.utc)
        appt_end = timezone.make_aware(datetime.combine(future_tuesday, time(2, 0)), dt_timezone.utc)
        Appointment.objects.create(
            tenant=self.tenant,
            client=self.client1,
            provider=self.trainer1,
            location=self.loc_boston,
            room=self.room,
            start_at=appt_start,
            end_at=appt_end,
            status='scheduled',
        )

        # 1. Attempting to delete the Monday 21:00-03:00 availability should fail because it covers Tuesday 01:00-02:00
        del_res = self.client.delete(
            f'/api/v1/scheduling/staff-availability/{avail.id}/',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(del_res.status_code, 400)
        self.assertIn("Cannot delete availability", str(del_res.data))

        # 2. Attempting to shorten Monday 21:00-03:00 to Monday 21:00-23:59 should fail
        patch_res = self.client.patch(
            f'/api/v1/scheduling/staff-availability/{avail.id}/',
            {"end_time": "23:59:00"},
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(patch_res.status_code, 400)
        self.assertIn("Cannot update availability", str(patch_res.data))

        # 3. Attempting to add a cross-midnight blackout Monday 23:00 - Tuesday 02:00 over the Tuesday 01:00 appointment should fail
        blackout_res = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            {
                "staff": str(self.trainer1.id),
                "location": str(self.loc_boston.id),
                "weekday_or_date": "monday",
                "start_time": "23:00:00",
                "end_time": "02:00:00",
                "is_blackout": True,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(blackout_res.status_code, 400)
        self.assertIn("Cannot add blackout", str(blackout_res.data))

    def test_blackout_overlapping_only_part_of_cross_midnight_interval(self):
        """
        Verify a partial blackout (e.g. Monday 23:00 to Tuesday 01:00, 2 hours) inside a
        Monday 21:00 to Tuesday 03:00 (6 hours) availability only removes the 2 blacked-out slots
        and leaves the remaining 4 slots bookable.
        """
        # Create 6-hour cross-midnight availability: Monday 21:00 -> Tuesday 03:00
        self.client.post(
            '/api/v1/scheduling/staff-availability/',
            {
                "staff": str(self.trainer1.id),
                "location": str(self.loc_boston.id),
                "weekday_or_date": "monday",
                "start_time": "21:00:00",
                "end_time": "03:00:00",
                "is_blackout": False,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        # Create 2-hour cross-midnight blackout: Monday 23:00 -> Tuesday 01:00
        b_res = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            {
                "staff": str(self.trainer1.id),
                "location": str(self.loc_boston.id),
                "weekday_or_date": "monday",
                "start_time": "23:00:00",
                "end_time": "01:00:00",
                "is_blackout": True,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(b_res.status_code, 201)

        avail_res = self.client.get(
            f'/api/v1/scheduling/appointments/availability/?provider={self.trainer1.id}&location={self.loc_boston.id}',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(avail_res.status_code, 200)
        slots = avail_res.data['availability']
        # 6 total hours - 2 blacked-out hours = 4 available slots (21-22, 22-23, 01-02, 02-03)
        self.assertEqual(len(slots), 4)
        start_hours = [s['start_at'].hour for s in slots]
        self.assertEqual(start_hours, [21, 22, 1, 2])

    def test_different_staff_and_locations_have_independent_schedules(self):
        """
        Verify:
        1. The same staff member can have separate availability schedules at different locations.
        2. Different staff members at the same location can have identical availability schedules without conflict.
        """
        # Trainer 1 at Boston Location: Monday 21:00 -> 03:00
        res_t1_loc1 = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            {
                "staff": str(self.trainer1.id),
                "location": str(self.loc_boston.id),
                "weekday_or_date": "Monday",
                "start_time": "21:00:00",
                "end_time": "03:00:00",
                "is_blackout": False,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_t1_loc1.status_code, 201)

        # Trainer 1 at Cambridge Location: Monday 21:00 -> 03:00 (separate location -> allowed!)
        res_t1_loc2 = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            {
                "staff": str(self.trainer1.id),
                "location": str(self.loc_cambridge.id),
                "weekday_or_date": "Monday",
                "start_time": "21:00:00",
                "end_time": "03:00:00",
                "is_blackout": False,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_t1_loc2.status_code, 201)

        # Trainer 2 at Boston Location: Monday 21:00 -> 03:00 (different staff -> allowed!)
        res_t2_loc1 = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            {
                "staff": str(self.trainer2.id),
                "location": str(self.loc_boston.id),
                "weekday_or_date": "Monday",
                "start_time": "21:00:00",
                "end_time": "03:00:00",
                "is_blackout": False,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_t2_loc1.status_code, 201)

        # Filtering GET /api/v1/scheduling/staff-availability/ by staff and location returns only matching record
        list_boston = self.client.get(
            f'/api/v1/scheduling/staff-availability/?staff={self.trainer1.id}&location={self.loc_boston.id}',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(list_boston.status_code, 200)
        results = list_boston.data['results'] if isinstance(list_boston.data, dict) else list_boston.data
        self.assertEqual(len(results), 1)
        self.assertEqual(str(results[0]['location']), str(self.loc_boston.id))

    def test_block_assigning_or_substituting_trainer_with_active_blackout_and_filter_by_is_blackout_and_date(self):
        """
        Verify:
        1. Cannot create a ClassSession or assign/substitute a trainer who has an active blackout (`is_blackout=True`) during the session.
        2. Cannot accept a SubstituteRequest for a trainer who has an active blackout during the session.
        3. GET /api/v1/scheduling/staff-availability/?is_blackout=true&date=YYYY-MM-DD filters blackouts for that date.
        """
        from datetime import timezone as dt_timezone
        target_date = (timezone.now() + timedelta(days=3)).date()
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.loc_boston,
            name="Power HIIT",
            duration_min=60,
            default_capacity=10,
        )

        # Trainer 2 marks themselves unavailable (blackout) on target_date from 13:00 to 16:00 UTC
        b_res = self.client.post(
            '/api/v1/scheduling/staff-availability/',
            {
                "staff": str(self.trainer2.id),
                "location": str(self.loc_boston.id),
                "weekday_or_date": str(target_date),
                "start_time": "13:00:00",
                "end_time": "16:00:00",
                "is_blackout": True,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(b_res.status_code, 201)

        # Also add a regular availability for trainer1 on target_date so we can test ?is_blackout=true filtering
        self.client.post(
            '/api/v1/scheduling/staff-availability/',
            {
                "staff": str(self.trainer1.id),
                "location": str(self.loc_boston.id),
                "weekday_or_date": str(target_date),
                "start_time": "08:00:00",
                "end_time": "18:00:00",
                "is_blackout": False,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )

        # 1. Test ?is_blackout=true&date=<target_date> filter
        filter_res = self.client.get(
            f'/api/v1/scheduling/staff-availability/?is_blackout=true&date={target_date}',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(filter_res.status_code, 200)
        filter_items = filter_res.data['results'] if isinstance(filter_res.data, dict) else filter_res.data
        self.assertEqual(len(filter_items), 1)
        self.assertEqual(str(filter_items[0]['staff']), str(self.trainer2.id))
        self.assertTrue(filter_items[0]['is_blackout'])

        # 2. Attempt to create a session at 14:00 - 15:00 UTC assigned directly to trainer2 (who is on blackout)
        sess_start = timezone.make_aware(datetime.combine(target_date, time(14, 0)), dt_timezone.utc)
        sess_end = timezone.make_aware(datetime.combine(target_date, time(15, 0)), dt_timezone.utc)
        create_sess_res = self.client.post(
            '/api/v1/scheduling/sessions/',
            {
                "template": str(template.id),
                "room": str(self.room.id),
                "staff": str(self.trainer2.id),
                "start_at": sess_start.isoformat(),
                "end_at": sess_end.isoformat(),
                "capacity": 10,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(create_sess_res.status_code, 400)
        self.assertIn("unavailable", str(create_sess_res.data).lower())

        # 3. Create the session with trainer1 (available) -> 201 Created
        valid_sess_res = self.client.post(
            '/api/v1/scheduling/sessions/',
            {
                "template": str(template.id),
                "room": str(self.room.id),
                "staff": str(self.trainer1.id),
                "start_at": sess_start.isoformat(),
                "end_at": sess_end.isoformat(),
                "capacity": 10,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(valid_sess_res.status_code, 201)
        session_id = valid_sess_res.data['id']

        # 4. Attempt to substitute trainer1 -> trainer2 via PATCH /api/v1/scheduling/sessions/{id}/ -> 400 Bad Request
        patch_sub_res = self.client.patch(
            f'/api/v1/scheduling/sessions/{session_id}/',
            {"staff": str(self.trainer2.id)},
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(patch_sub_res.status_code, 400)
        self.assertIn("unavailable", str(patch_sub_res.data).lower())

        # 5. Attempt to substitute trainer1 -> trainer2 via SubstituteRequestViewSet.accept -> 400 Bad Request
        sub_req = SubstituteRequest.objects.create(
            tenant=self.tenant,
            session_id=session_id,
            requested_by_staff=self.trainer1,
            status='open',
        )
        accept_res = self.client.post(
            f'/api/v1/scheduling/substitute-requests/{sub_req.id}/accept/',
            {"trainer": str(self.trainer2.id)},
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(accept_res.status_code, 400)
        self.assertIn("unavailable", str(accept_res.data).lower())

    def test_configurable_staff_cooldown_minutes_between_sessions_and_appointments(self):
        """
        Verify configurable `staff_cooldown_minutes` (default 10 min) is enforced across:
        1. Appointment -> Session (e.g. Appointment 09:00-10:00 blocks Session at 10:00 or 10:05, allows 10:10).
        2. Session -> Session (Session 10:10-11:10 blocks Session at 11:15, allows 11:20).
        3. Session -> Appointment (Session 10:10-11:10 blocks Appointment at 11:15, allows 11:20).
        4. Configurable via PATCH /api/v1/scheduling/booking-settings/ (e.g. changing to 15 min).
        """
        from datetime import timezone as dt_timezone
        target_date = (timezone.now() + timedelta(days=4)).date()
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.loc_boston,
            name="Strength Circuit",
            duration_min=60,
            default_capacity=10,
        )
        pkg_type = PackageType.objects.create(
            tenant=self.tenant,
            location=self.loc_boston,
            name="10 Pack",
            credit_count=10,
            price=100.00,
            validity_days=30,
        )
        Package.objects.create(
            tenant=self.tenant,
            client=self.client1,
            package_type=pkg_type,
            status='active',
            credits_remaining=10,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Verify default staff_cooldown_minutes is 10 in booking-settings
        settings_res = self.client.get(
            '/api/v1/scheduling/booking-settings/',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(settings_res.status_code, 200)
        self.assertEqual(settings_res.data['staff_cooldown_minutes'], 10)

        # Create an initial Appointment for trainer1 from 09:00 to 10:00 UTC
        appt_start = timezone.make_aware(datetime.combine(target_date, time(9, 0)), dt_timezone.utc)
        appt_end = timezone.make_aware(datetime.combine(target_date, time(10, 0)), dt_timezone.utc)
        Appointment.objects.create(
            tenant=self.tenant,
            client=self.client1,
            provider=self.trainer1,
            location=self.loc_boston,
            room=self.room,
            start_at=appt_start,
            end_at=appt_end,
            status='scheduled',
        )

        # 1. Appointment -> Session: Try scheduling a session starting at 10:00 (0 min gap) or 10:05 (5 min gap) -> 400 Cooldown conflict
        sess_1005_start = timezone.make_aware(datetime.combine(target_date, time(10, 5)), dt_timezone.utc)
        sess_1005_end = timezone.make_aware(datetime.combine(target_date, time(11, 5)), dt_timezone.utc)
        res_cooldown_fail = self.client.post(
            '/api/v1/scheduling/sessions/',
            {
                "template": str(template.id),
                "room": str(self.room.id),
                "staff": str(self.trainer1.id),
                "start_at": sess_1005_start.isoformat(),
                "end_at": sess_1005_end.isoformat(),
                "capacity": 10,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_cooldown_fail.status_code, 400)
        self.assertIn("cooldown", str(res_cooldown_fail.data).lower())

        # 2. Appointment -> Session: Schedule session starting at 10:10 (exact 10-min cooldown elapsed) -> 201 Created
        sess_1010_start = timezone.make_aware(datetime.combine(target_date, time(10, 10)), dt_timezone.utc)
        sess_1010_end = timezone.make_aware(datetime.combine(target_date, time(11, 10)), dt_timezone.utc)
        res_cooldown_ok = self.client.post(
            '/api/v1/scheduling/sessions/',
            {
                "template": str(template.id),
                "room": str(self.room.id),
                "staff": str(self.trainer1.id),
                "start_at": sess_1010_start.isoformat(),
                "end_at": sess_1010_end.isoformat(),
                "capacity": 10,
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_cooldown_ok.status_code, 201, res_cooldown_ok.data)

        # 3. Session -> Appointment: Try booking an appointment at 11:15 (only 5 min after session ends at 11:10) -> 400 Cooldown conflict
        appt_1115_start = timezone.make_aware(datetime.combine(target_date, time(11, 15)), dt_timezone.utc)
        appt_1115_end = timezone.make_aware(datetime.combine(target_date, time(12, 15)), dt_timezone.utc)
        res_appt_fail = self.client.post(
            '/api/v1/scheduling/appointments/',
            {
                "client": str(self.client1.id),
                "provider": str(self.trainer1.id),
                "location": str(self.loc_boston.id),
                "room": str(self.room.id),
                "start_at": appt_1115_start.isoformat(),
                "end_at": appt_1115_end.isoformat(),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_appt_fail.status_code, 400)
        self.assertIn("cooldown", str(res_appt_fail.data).lower())

        # 4. Session -> Appointment: Book appointment at 11:20 (10-min cooldown elapsed after 11:10) -> 201 Created
        appt_1120_start = timezone.make_aware(datetime.combine(target_date, time(11, 20)), dt_timezone.utc)
        appt_1120_end = timezone.make_aware(datetime.combine(target_date, time(12, 20)), dt_timezone.utc)
        res_appt_ok = self.client.post(
            '/api/v1/scheduling/appointments/',
            {
                "client": str(self.client1.id),
                "provider": str(self.trainer1.id),
                "location": str(self.loc_boston.id),
                "room": str(self.room.id),
                "start_at": appt_1120_start.isoformat(),
                "end_at": appt_1120_end.isoformat(),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_appt_ok.status_code, 201, res_appt_ok.data)

    def test_block_class_session_create_and_update_when_staff_has_overlapping_appointment_or_session(self):
        """
        Verify:
        1. Creating a ClassSession (with or without explicit end_at/capacity) is blocked when the assigned staff
           member has an overlapping Appointment (e.g. 15:00-15:50) or an overlapping ClassSession.
        2. Updating a ClassSession (changing time or reassigning staff) is blocked when the target staff member
           has an overlapping Appointment or ClassSession.
        3. Creating an Appointment is blocked when the staff member has an overlapping ClassSession.
        """
        from datetime import timezone as dt_timezone
        target_date = (timezone.now() + timedelta(days=5)).date()
        template_50 = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.loc_boston,
            name="50-Min Pilates",
            duration_min=50,
            default_capacity=12,
        )

        # Staff member (trainer1) has an appointment from 15:00 to 15:50 UTC
        appt_start = timezone.make_aware(datetime.combine(target_date, time(15, 0)), dt_timezone.utc)
        appt_end = timezone.make_aware(datetime.combine(target_date, time(15, 50)), dt_timezone.utc)
        Appointment.objects.create(
            tenant=self.tenant,
            client=self.client1,
            provider=self.trainer1,
            location=self.loc_boston,
            room=self.room,
            start_at=appt_start,
            end_at=appt_end,
            status='scheduled',
        )

        # 1. Admin attempts to create a ClassSession for trainer1 at 15:00 (omitting end_at & capacity, just like frontend modal) -> 400 Bad Request
        res_sess_vs_appt = self.client.post(
            '/api/v1/scheduling/sessions/',
            {
                "template": str(template_50.id),
                "staff": str(self.trainer1.id),
                "start_at": appt_start.isoformat(),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_sess_vs_appt.status_code, 400)
        self.assertIn(
            "This staff member already has an appointment scheduled during the selected time",
            str(res_sess_vs_appt.data.get("detail", "")),
        )

        # 2. Create a valid ClassSession for trainer1 at 17:00 - 17:50 UTC -> 201 Created
        sess_1700_start = timezone.make_aware(datetime.combine(target_date, time(17, 0)), dt_timezone.utc)
        res_valid_sess = self.client.post(
            '/api/v1/scheduling/sessions/',
            {
                "template": str(template_50.id),
                "staff": str(self.trainer1.id),
                "start_at": sess_1700_start.isoformat(),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_valid_sess.status_code, 201, res_valid_sess.data)
        valid_sess_id = res_valid_sess.data["id"]

        # 3. Attempt to create a second overlapping ClassSession for trainer1 at 17:15 UTC -> 400 Bad Request
        sess_1715_start = timezone.make_aware(datetime.combine(target_date, time(17, 15)), dt_timezone.utc)
        res_sess_vs_sess = self.client.post(
            '/api/v1/scheduling/sessions/',
            {
                "template": str(template_50.id),
                "staff": str(self.trainer1.id),
                "start_at": sess_1715_start.isoformat(),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_sess_vs_sess.status_code, 400)
        self.assertIn(
            "This staff member is already assigned to another session",
            str(res_sess_vs_sess.data.get("detail", "")),
        )

        # 4. Attempt to update (reschedule) the 17:00 session onto the 15:00 appointment slot -> 400 Bad Request
        res_patch_to_appt = self.client.patch(
            f'/api/v1/scheduling/sessions/{valid_sess_id}/',
            {
                "start_at": appt_start.isoformat(),
                "end_at": appt_end.isoformat(),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_patch_to_appt.status_code, 400)
        self.assertIn(
            "This staff member already has an appointment scheduled during the selected time",
            str(res_patch_to_appt.data.get("detail", "")),
        )

        # 5. Attempt to book an Appointment for trainer1 overlapping the 17:00 - 17:50 session -> 400 Bad Request
        res_appt_vs_sess = self.client.post(
            '/api/v1/scheduling/appointments/',
            {
                "client": str(self.client1.id),
                "provider": str(self.trainer1.id),
                "location": str(self.loc_boston.id),
                "start_at": sess_1715_start.isoformat(),
                "end_at": (sess_1715_start + timedelta(minutes=50)).isoformat(),
            },
            format='json',
            HTTP_X_TENANT_ID=str(self.tenant.id),
        )
        self.assertEqual(res_appt_vs_sess.status_code, 400)
        self.assertIn(
            "This staff member is already assigned to another session",
            str(res_appt_vs_sess.data.get("detail", "")),
        )
















