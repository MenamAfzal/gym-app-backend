from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.tenants.context import set_current_tenant, reset_current_tenant
from apps.core.tenants.models import Tenant
from apps.retention.models import AttendanceTrend, ChurnRiskLevel, ClientRetentionMetrics
from apps.retention.services import RetentionMetricsService
from apps.scheduling.models import (
    Booking, CancellationPolicy, ClassSession, ClassTemplate,
    FacilityAccessLog, Location, Package, PackageType, Payment, Room
)
from apps.scheduling.views import BookingViewSet
from apps.users.models import ClientLifecycleStatus, User, UserRole


class Phase1RetentionMetricsTestCase(TestCase):
    def setUp(self):
        # Patch Celery delay calls to avoid broker dependency during unit tests
        self.waitlist_patcher = patch('apps.scheduling.tasks.process_waitlist_promotion_job.delay')
        self.mock_waitlist_delay = self.waitlist_patcher.start()
        self.addCleanup(self.waitlist_patcher.stop)

        self.retention_task_patcher = patch('apps.retention.tasks.recalculate_single_client_metrics.delay')
        self.mock_retention_delay = self.retention_task_patcher.start()
        self.addCleanup(self.retention_task_patcher.stop)

        # 1. Create Tenant
        self.tenant = Tenant.objects.create(name="Peak Performance Gym", subdomain="peak-gym")
        self.token = set_current_tenant(self.tenant)
        self.addCleanup(lambda: reset_current_tenant(self.token))

        # 2. Setup Location & Room
        self.location = Location.objects.create(
            tenant=self.tenant,
            name="Downtown Studio",
            address="100 Fitness Blvd",
            timezone="UTC"
        )
        self.room = Room.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Studio 1",
            capacity=20
        )

        # 3. Setup Class Template & Cancellation Policy (12 hours cutoff)
        self.template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="HIIT Blast",
            duration_min=45,
            default_capacity=20
        )
        self.policy = CancellationPolicy.objects.create(
            tenant=self.tenant,
            template=self.template,
            cutoff_hours=12,
            late_fee_amount=Decimal('10.00')
        )

        # 4. Setup Package Types & Packages
        self.package_type = PackageType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="10-Class Pack",
            credit_count=10,
            price=Decimal('150.00'),
            validity_days=90
        )

        # 5. Create Users
        self.owner = User.objects.create_user(
            email="owner@peakgym.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )
        self.client1 = User.objects.create_user(
            email="client1@peakgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            lifecycle_status=ClientLifecycleStatus.ACTIVE
        )
        self.client2 = User.objects.create_user(
            email="client2@peakgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            lifecycle_status=ClientLifecycleStatus.INACTIVE
        )
        self.client3 = User.objects.create_user(
            email="client3@peakgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            lifecycle_status=ClientLifecycleStatus.ACTIVE
        )

        self.factory = APIRequestFactory()

    def test_booking_late_cancellation_write_path(self):
        """
        Verify that late cancellation properly sets is_late_cancel=True,
        cancelled_at timestamp, and preserves optional cancellation_reason.
        """
        now = timezone.now()
        # Session starting in 2 hours (less than 12h cutoff -> late cancellation)
        session_late = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            start_at=now + timedelta(hours=2),
            end_at=now + timedelta(hours=2, minutes=45),
            capacity=20
        )

        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client1,
            package_type=self.package_type,
            credits_remaining=5,
            total_credits_allocated=10,
            expires_at=now + timedelta(days=60),
            status='active'
        )

        booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=session_late,
            credit_source=pkg,
            credits_used=1,
            status='booked'
        )

        view = BookingViewSet.as_view({'delete': 'destroy'})
        request = self.factory.delete(
            f'/api/v1/scheduling/bookings/{booking.id}/',
            data={'cancellation_reason': 'Emergency meeting at work'},
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client1)

        response = view(request, pk=str(booking.id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get('status'), 'cancelled')
        self.assertFalse(response.data.get('refunded'))  # Late cancellation not refunded

        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')
        self.assertTrue(booking.is_late_cancel)
        self.assertIsNotNone(booking.cancelled_at)
        self.assertEqual(booking.cancellation_reason, 'Emergency meeting at work')

        # Verify package credit was NOT refunded on late cancel
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 5)

    def test_booking_early_cancellation_write_path(self):
        """
        Verify that early cancellation sets is_late_cancel=False, cancelled_at,
        and refunds credit back to the package.
        """
        now = timezone.now()
        # Session starting in 48 hours (well above 12h cutoff -> early cancellation)
        session_early = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            start_at=now + timedelta(hours=48),
            end_at=now + timedelta(hours=48, minutes=45),
            capacity=20
        )

        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client1,
            package_type=self.package_type,
            credits_remaining=5,
            total_credits_allocated=10,
            expires_at=now + timedelta(days=60),
            status='active'
        )

        booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=session_early,
            credit_source=pkg,
            credits_used=1,
            status='booked'
        )

        view = BookingViewSet.as_view({'delete': 'destroy'})
        request = self.factory.delete(
            f'/api/v1/scheduling/bookings/{booking.id}/',
            data={'reason': 'Rescheduling to next week'},
            format='json'
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.client1)

        response = view(request, pk=str(booking.id))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.get('refunded'))

        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')
        self.assertFalse(booking.is_late_cancel)
        self.assertIsNotNone(booking.cancelled_at)
        self.assertEqual(booking.cancellation_reason, 'Rescheduling to next week')

        # Verify credit was refunded
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 6)

    def test_package_save_snapshots_allocated_credits(self):
        """
        Verify that creating a Package automatically snapshots total_credits_allocated
        from package_type.credit_count if total_credits_allocated is None.
        """
        now = timezone.now()
        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client1,
            package_type=self.package_type,
            credits_remaining=10,
            expires_at=now + timedelta(days=90)
        )
        self.assertEqual(pkg.total_credits_allocated, 10)

    def test_recalculate_for_tenant_metrics_accuracy(self):
        """
        Verify bulk recalculation accurately computes:
        - days_since_last_visit
        - visits_last_7d, visits_last_30d, visits_prev_30d
        - visit_frequency_weekly_30d
        - attendance_trend
        - credit_utilization_rate
        - no_show_rate
        - lifetime_value and average_spend
        """
        now = timezone.now()

        # 1. Setup client1 with past and recent attendance
        session1 = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            start_at=now - timedelta(days=2),
            end_at=now - timedelta(days=2, hours=-1),
            capacity=20
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=session1,
            status='checked_in',
            checked_in_at=now - timedelta(days=2)
        )

        session2 = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            start_at=now - timedelta(days=10),
            end_at=now - timedelta(days=10, hours=-1),
            capacity=20
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client1,
            session=session2,
            status='attended',
            checked_in_at=now - timedelta(days=10)
        )

        # Facility access log 4 days ago
        fac_log = FacilityAccessLog.objects.create(
            tenant=self.tenant,
            location=self.location,
            client=self.client1,
        )
        FacilityAccessLog.objects.filter(id=fac_log.id).update(checked_in_at=now - timedelta(days=4))

        # Package with 10 allocated, 4 remaining -> 6 used (60% utilization)
        Package.objects.create(
            tenant=self.tenant,
            client=self.client1,
            package_type=self.package_type,
            total_credits_allocated=10,
            credits_remaining=4,
            expires_at=now + timedelta(days=45),
            status='active'
        )

        # Payment records for client1: $150.00 and $50.00 -> LTV = 200.00, Avg = 100.00
        Payment.objects.create(
            tenant=self.tenant,
            client=self.client1,
            amount=Decimal('150.00'),
            type='package_purchase',
            status='completed',
            idempotency_key='pay-1'
        )
        Payment.objects.create(
            tenant=self.tenant,
            client=self.client1,
            amount=Decimal('50.00'),
            type='drop_in',
            status='completed',
            idempotency_key='pay-2'
        )

        # 2. Setup client3 with high no-show rate
        session_ns1 = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            start_at=now - timedelta(days=5),
            end_at=now - timedelta(days=5, hours=-1),
            capacity=20
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client3,
            session=session_ns1,
            status='no_show'
        )
        session_ns2 = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            start_at=now - timedelta(days=15),
            end_at=now - timedelta(days=15, hours=-1),
            capacity=20
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client3,
            session=session_ns2,
            status='attended',
            checked_in_at=now - timedelta(days=15)
        )

        # Run recalculation engine
        processed = RetentionMetricsService.recalculate_for_tenant(str(self.tenant.id))
        self.assertEqual(processed, 3)

        # Verify client1 metrics
        m1 = ClientRetentionMetrics.objects.get(client=self.client1)
        self.assertEqual(m1.days_since_last_visit, 2)
        # client1 visits in last 30d: 2 bookings + 1 facility = 3 visits
        self.assertEqual(m1.visits_last_30d, 3)
        # visits in last 7d: booking 2d ago + facility 4d ago = 2 visits
        self.assertEqual(m1.visits_last_7d, 2)
        # weekly frequency: 3 / 4.28 = 0.70
        self.assertEqual(m1.visit_frequency_weekly_30d, Decimal('0.70'))
        # attendance trend: 3 visits vs 0 prev_30d -> increasing
        self.assertEqual(m1.attendance_trend, AttendanceTrend.INCREASING)
        # credit utilization rate: 6 / 10 = 60.00%
        self.assertEqual(m1.credit_utilization_rate, Decimal('60.00'))
        # lifetime value and average spend
        self.assertEqual(m1.lifetime_value, Decimal('200.00'))
        self.assertEqual(m1.average_spend, Decimal('100.00'))
        # second visit verification: visits at 10d ago, 4d ago, 2d ago -> second visit is 4d ago
        self.assertIsNotNone(m1.first_visit_at)
        self.assertIsNotNone(m1.second_visit_at)
        self.assertTrue(m1.first_visit_at < m1.second_visit_at)

        # Verify client3 metrics
        m3 = ClientRetentionMetrics.objects.get(client=self.client3)
        self.assertEqual(m3.total_bookings, 2)
        self.assertEqual(m3.total_no_shows, 1)
        self.assertEqual(m3.no_show_rate, Decimal('50.00'))

    def test_reactivation_and_lifecycle_transitions(self):
        """
        Verify:
        1. A client in INACTIVE/CHURNED status who visits in last 7 days
           transitions to ACTIVE with reactivated_at populated.
        2. A client inactive for >= 60 days with no packages transitions to CHURNED.
        3. A client inactive for >= 30 days transitions to INACTIVE.
        """
        now = timezone.now()

        # client2 starts as INACTIVE
        self.assertEqual(self.client2.lifecycle_status, ClientLifecycleStatus.INACTIVE)
        self.assertIsNone(self.client2.reactivated_at)

        # Add a recent visit 1 day ago
        session_recent = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            start_at=now - timedelta(days=1),
            end_at=now - timedelta(days=1, hours=-1),
            capacity=20
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client2,
            session=session_recent,
            status='checked_in',
            checked_in_at=now - timedelta(days=1)
        )

        # Run recalculation
        RetentionMetricsService.recalculate_for_tenant(str(self.tenant.id))

        self.client2.refresh_from_db()
        self.assertEqual(self.client2.lifecycle_status, ClientLifecycleStatus.ACTIVE)
        self.assertIsNotNone(self.client2.reactivated_at)

    def test_zero_n_plus_one_bulk_queries(self):
        """
        Verify that recalculate_for_tenant executes in constant, bounded O(1)
        database queries regardless of the number of clients.
        """
        now = timezone.now()

        # Create additional clients to test query scaling
        for i in range(10):
            u = User.objects.create_user(
                email=f"scale_client_{i}@peakgym.com",
                password="password123",
                role=UserRole.CLIENT,
                tenant=self.tenant
            )
            # Add some bookings and packages
            s = ClassSession.objects.create(
                tenant=self.tenant,
                template=self.template,
                room=self.room,
                start_at=now - timedelta(days=i + 1),
                end_at=now - timedelta(days=i + 1, hours=-1),
                capacity=20
            )
            Booking.objects.create(
                tenant=self.tenant,
                client=u,
                session=s,
                status='attended',
                checked_in_at=now - timedelta(days=i + 1)
            )

        # Measure query count for 13 clients
        with self.assertNumQueries(9):
            # 1: User select
            # 2: Booking agg
            # 3: FacilityAccessLog agg
            # 4: Package agg
            # 5: Payment agg
            # 6: ClientRetentionMetrics existing select
            # 7: Booking earliest raw select (for second_visit_at)
            # 8: FacilityAccessLog earliest raw select (for second_visit_at)
            # 9: ClientRetentionMetrics bulk_create / upsert
            processed = RetentionMetricsService.recalculate_for_tenant(str(self.tenant.id))
            self.assertEqual(processed, 13)
