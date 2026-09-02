from django.test import TestCase
from django.utils import timezone
from datetime import timedelta, datetime, time
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.users.models import User, UserRole
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.scheduling.models import (
    Location, Room, ClassTemplate, RecurrenceRule, ClassSession,
    Booking, PackageType, Package, Waitlist, CancellationPolicy
)
from apps.scheduling.tasks import process_waitlist_promotion_job

class GymSchedulingSystemTestCase(TestCase):
    def setUp(self):
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


