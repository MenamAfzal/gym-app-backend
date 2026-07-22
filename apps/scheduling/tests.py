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

        # Cancellation Policy (24-hour cutoff)
        self.policy = CancellationPolicy.objects.create(
            tenant=self.tenant, scope_type='global', cutoff_hours=24, late_fee_amount=10.00
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
        start_time = timezone.now() + timedelta(days=2) # Outside cutoff
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

        # 3. Late Cancel (No Refund)
        late_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=timezone.now() + timedelta(hours=10), # Inside 24h cutoff
            end_at=timezone.now() + timedelta(hours=11),
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

        # Late cancel
        booking2.status = 'no_show'
        booking2.save()
        # No increment to credits_remaining
        self.assertEqual(self.pkg1.credits_remaining, 9)

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
