import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.tenants.context import set_current_tenant, reset_current_tenant
from apps.core.tenants.models import Tenant
from apps.retention.ai_service import RetentionAIService
from apps.retention.models import (
    AttendanceTrend, ChurnRiskLevel, ClientRetentionMetrics, SavedSegment
)
from apps.retention.services import (
    CohortAnalyticsService, FunnelAnalyticsService,
    OperationalAnalyticsService, RetentionMetricsService, SegmentQueryService
)
from apps.retention.views import (
    AtRiskSummaryView,
    ClassUtilizationAnalyticsView,
    ClientRetentionMetricsViewSet,
    CohortRetentionMatrixView,
    CustomerJourneyFunnelView,
    StaffPerformanceAnalyticsView,
)
from apps.scheduling.models import (
    Booking, ClassSession, ClassTemplate, FacilityAccessLog,
    Location, Package, PackageType, Payment, Room
)
from apps.users.models import ClientLifecycleStatus, User, UserProfile, UserRole


class Phase2RetentionIntelligenceTestCase(TestCase):
    def setUp(self):
        # Patch Celery tasks
        self.waitlist_patcher = patch('apps.scheduling.tasks.process_waitlist_promotion_job.delay')
        self.mock_waitlist_delay = self.waitlist_patcher.start()
        self.addCleanup(self.waitlist_patcher.stop)

        self.retention_task_patcher = patch('apps.retention.tasks.recalculate_single_client_metrics.delay')
        self.mock_retention_delay = self.retention_task_patcher.start()
        self.addCleanup(self.retention_task_patcher.stop)

        # 1. Primary Tenant Setup
        self.tenant = Tenant.objects.create(name="Peak Performance Gym", subdomain="peak-gym-p2")
        self.token = set_current_tenant(self.tenant)
        self.addCleanup(lambda: reset_current_tenant(self.token))

        # 2. Location & Room
        self.location = Location.objects.create(
            tenant=self.tenant,
            name="Main Studio",
            address="123 Fitness Ave",
            timezone="UTC"
        )
        self.room = Room.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Yoga Room",
            capacity=20
        )

        # 3. Class Template
        self.template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Vinyasa Flow",
            duration_min=60,
            default_capacity=15
        )

        # 4. Package Types
        self.pkg_type_monthly = PackageType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Monthly Unlimited",
            credit_count=30,
            price=Decimal('120.00'),
            billing_cycle='monthly',
            validity_days=30
        )
        self.pkg_type_weekly = PackageType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Weekly Pass",
            credit_count=5,
            price=Decimal('35.00'),
            billing_cycle='weekly',
            validity_days=7
        )

        # 5. Users
        self.owner = User.objects.create_user(
            email="owner@peakp2.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )
        self.trainer = User.objects.create_user(
            email="trainer@peakp2.com",
            password="password123",
            role=UserRole.TRAINER,
            tenant=self.tenant
        )

        # Clients
        self.client_vip = User.objects.create_user(
            email="vip@peakp2.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            lifecycle_status=ClientLifecycleStatus.ACTIVE
        )
        self.client_risk = User.objects.create_user(
            email="risk@peakp2.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            lifecycle_status=ClientLifecycleStatus.AT_RISK
        )
        self.client_normal = User.objects.create_user(
            email="normal@peakp2.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            lifecycle_status=ClientLifecycleStatus.ACTIVE
        )

        self.factory = APIRequestFactory()

    def test_task2_metrics_calculations(self):
        """
        Verify RetentionMetricsService calculates:
        - failed_payments_last_90d and adds +15 churn score weight
        - estimated_monthly_value (active package normalization or LTV fallback)
        - at_risk_revenue for HIGH/CRITICAL or at_risk lifecycle clients
        - is_high_value big spender threshold
        """
        now = timezone.now()

        # 1. client_vip: High spender with completed payments and monthly active package
        Package.objects.create(
            tenant=self.tenant,
            client=self.client_vip,
            package_type=self.pkg_type_monthly,
            price=Decimal('120.00'),
            credits_remaining=20,
            total_credits_allocated=30,
            expires_at=now + timedelta(days=20),
            status='active'
        )
        Payment.objects.create(
            tenant=self.tenant,
            client=self.client_vip,
            amount=Decimal('600.00'),
            type='package_purchase',
            status='completed',
            idempotency_key=str(uuid.uuid4())
        )

        # 2. client_risk: Inactive, failed payment, at-risk lifecycle, weekly package
        Package.objects.create(
            tenant=self.tenant,
            client=self.client_risk,
            package_type=self.pkg_type_weekly,
            price=Decimal('20.00'),
            credits_remaining=5,
            total_credits_allocated=5,
            expires_at=now + timedelta(days=4),
            status='active'
        )
        Payment.objects.create(
            tenant=self.tenant,
            client=self.client_risk,
            amount=Decimal('20.00'),
            type='package_purchase',
            status='failed',
            idempotency_key=str(uuid.uuid4())
        )
        # Add a past booking 35 days ago to trigger inactivity churn score
        session_past = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            start_at=now - timedelta(days=35),
            end_at=now - timedelta(days=35, minutes=-60),
            capacity=15
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_risk,
            session=session_past,
            status='attended',
            checked_in_at=now - timedelta(days=35)
        )

        # Recalculate metrics for tenant
        processed = RetentionMetricsService.recalculate_for_tenant(str(self.tenant.id))
        self.assertGreaterEqual(processed, 3)

        # Verify client_risk metrics
        m_risk = ClientRetentionMetrics.objects.get(client=self.client_risk)
        self.assertEqual(m_risk.failed_payments_last_90d, 1)
        self.assertIn("Recent failed payment", m_risk.risk_factors)
        self.assertIn("No visits in the last 30 days", m_risk.risk_factors)
        self.assertIn(m_risk.risk_level, [ChurnRiskLevel.HIGH, ChurnRiskLevel.CRITICAL])
        # Weekly pass estimated monthly value = 20 * 4.33 = 86.60
        self.assertEqual(m_risk.estimated_monthly_value, Decimal('86.60'))
        # At risk revenue should match estimated monthly value for high/critical or at-risk
        self.assertEqual(m_risk.at_risk_revenue, Decimal('86.60'))

        # Verify client_vip metrics
        m_vip = ClientRetentionMetrics.objects.get(client=self.client_vip)
        self.assertEqual(m_vip.failed_payments_last_90d, 0)
        self.assertEqual(m_vip.estimated_monthly_value, Decimal('120.00'))
        self.assertTrue(m_vip.is_high_value)  # LTV >= 500 when < 5 paying clients

        # Verify SegmentQueryService criteria filtering
        qs = ClientRetentionMetrics.objects.filter(tenant=self.tenant)
        self.assertEqual(SegmentQueryService.apply_criteria(qs, {'is_high_value': True}).count(), 1)
        self.assertEqual(SegmentQueryService.apply_criteria(qs, {'has_failed_payments': True}).count(), 1)
        self.assertEqual(SegmentQueryService.apply_criteria(qs, {'min_at_risk_revenue': 80}).count(), 1)
        self.assertEqual(SegmentQueryService.apply_criteria(qs, {'min_lifetime_value': 500}).count(), 1)

    def test_task3_customer_journey_funnel_api(self):
        """
        Verify FunnelAnalyticsService and GET /api/v1/retention/analytics/funnel/
        computes 5 sequential stages and 1st-to-2nd visit conversion math accurately.
        """
        now = timezone.now()

        # Create clients in acquisition cohort
        c1 = User.objects.create_user(
            email="funnel1@peakp2.com", password="pass", role=UserRole.CLIENT, tenant=self.tenant
        )
        c2 = User.objects.create_user(
            email="funnel2@peakp2.com", password="pass", role=UserRole.CLIENT, tenant=self.tenant
        )
        c3 = User.objects.create_user(
            email="funnel3@peakp2.com", password="pass", role=UserRole.CLIENT, tenant=self.tenant
        )

        # Stage 2: Packages
        Package.objects.create(
            tenant=self.tenant, client=c1, package_type=self.pkg_type_monthly,
            credits_remaining=10, expires_at=now + timedelta(days=30), status='active'
        )
        Package.objects.create(
            tenant=self.tenant, client=c2, package_type=self.pkg_type_weekly,
            credits_remaining=5, expires_at=now + timedelta(days=7), status='active'
        )

        # Stage 3 & 4: Bookings for visits
        sess1 = ClassSession.objects.create(
            tenant=self.tenant, template=self.template, room=self.room,
            start_at=now - timedelta(days=10), end_at=now - timedelta(days=10, minutes=-60), capacity=15
        )
        sess2 = ClassSession.objects.create(
            tenant=self.tenant, template=self.template, room=self.room,
            start_at=now - timedelta(days=6), end_at=now - timedelta(days=6, minutes=-60), capacity=15
        )

        # c1 has 2 visits (days 10 and 6)
        Booking.objects.create(tenant=self.tenant, client=c1, session=sess1, status='attended', checked_in_at=now - timedelta(days=10))
        Booking.objects.create(tenant=self.tenant, client=c1, session=sess2, status='attended', checked_in_at=now - timedelta(days=6))

        # c2 has 1 visit (day 10)
        Booking.objects.create(tenant=self.tenant, client=c2, session=sess1, status='attended', checked_in_at=now - timedelta(days=10))

        # Stage 5: c1 is converted active member
        c1.converted_at = now - timedelta(days=5)
        c1.save()

        # Recalculate metrics so first_visit_at and second_visit_at are populated
        RetentionMetricsService.recalculate_for_tenant(str(self.tenant.id))

        # Call endpoint via APIRequestFactory
        view = CustomerJourneyFunnelView.as_view()
        req = self.factory.get('/api/v1/retention/analytics/funnel/?days=30')
        req.tenant = self.tenant
        force_authenticate(req, user=self.owner)
        resp = view(req)

        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn('stages', data)
        self.assertIn('first_to_second_visit_conversion', data)

        # Check stage counts
        self.assertGreaterEqual(data['total_leads_acquired'], 3)
        self.assertGreaterEqual(data['intro_or_package_purchased'], 2)
        self.assertGreaterEqual(data['first_visit_completed'], 2)
        self.assertGreaterEqual(data['second_visit_completed'], 1)
        self.assertGreaterEqual(data['converted_to_active_member'], 1)

        f2s = data['first_to_second_visit_conversion']
        self.assertIn('conversion_rate_percent', f2s)
        self.assertIn('avg_days_between_first_and_second_visit', f2s)
        # c1 had 4 days between visit 1 and 2
        self.assertAlmostEqual(f2s['avg_days_between_first_and_second_visit'], 4.0, delta=0.5)

    def test_task4_cohort_retention_matrix_api(self):
        """
        Verify CohortAnalyticsService and GET /api/v1/retention/analytics/cohorts/
        accurately groups clients into monthly cohorts and tracks M0, M1 retention.
        """
        now = timezone.now()

        # Create client joining current month
        c_current = User.objects.create_user(
            email="cohort_curr@peakp2.com", password="pass", role=UserRole.CLIENT,
            tenant=self.tenant, date_joined=now
        )
        # Attended class this month
        sess_now = ClassSession.objects.create(
            tenant=self.tenant, template=self.template, room=self.room,
            start_at=now, end_at=now + timedelta(minutes=60), capacity=15
        )
        Booking.objects.create(tenant=self.tenant, client=c_current, session=sess_now, status='attended')

        view = CohortRetentionMatrixView.as_view()
        req = self.factory.get('/api/v1/retention/analytics/cohorts/?months=3')
        req.tenant = self.tenant
        force_authenticate(req, user=self.owner)
        resp = view(req)

        self.assertEqual(resp.status_code, 200)
        data = resp.data
        self.assertIn('cohorts', data)
        self.assertEqual(len(data['cohorts']), 3)

        curr_cohort = data['cohorts'][-1]
        self.assertEqual(curr_cohort['cohort_month'], f"{now.year:04d}-{now.month:02d}")
        self.assertGreaterEqual(curr_cohort['initial_size'], 1)
        self.assertGreaterEqual(len(curr_cohort['retention_by_month']), 1)
        self.assertEqual(curr_cohort['retention_by_month'][0]['month_offset'], 0)
        self.assertGreater(curr_cohort['retention_by_month'][0]['active_count'], 0)

    def test_task5_class_utilization_and_staff_performance_api(self):
        """
        Verify OperationalAnalyticsService endpoints:
        - GET /api/v1/retention/analytics/class-utilization/
        - GET /api/v1/retention/analytics/staff-performance/
        """
        now = timezone.now()

        # Session taught by self.trainer
        sess = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            staff=self.trainer,
            start_at=now - timedelta(days=2),
            end_at=now - timedelta(days=2, minutes=-60),
            capacity=10
        )
        # 2 attended bookings, 1 cancelled, 1 no-show
        Booking.objects.create(tenant=self.tenant, client=self.client_vip, session=sess, status='attended')
        Booking.objects.create(tenant=self.tenant, client=self.client_normal, session=sess, status='attended')
        Booking.objects.create(tenant=self.tenant, client=self.client_risk, session=sess, status='cancelled')

        # Assigned client to trainer
        profile, _ = UserProfile.objects.get_or_create(user=self.client_vip)
        profile.assigned_trainer = self.trainer
        profile.save()

        RetentionMetricsService.recalculate_for_tenant(str(self.tenant.id))

        # 1. Test class utilization endpoint
        util_view = ClassUtilizationAnalyticsView.as_view()
        req1 = self.factory.get('/api/v1/retention/analytics/class-utilization/?days=30')
        req1.tenant = self.tenant
        force_authenticate(req1, user=self.owner)
        resp1 = util_view(req1)

        self.assertEqual(resp1.status_code, 200)
        d1 = resp1.data
        self.assertEqual(d1['total_sessions_held'], 1)
        self.assertEqual(d1['total_capacity'], 10)
        self.assertEqual(d1['total_attended'], 2)
        self.assertEqual(d1['total_cancellations'], 1)
        # 2 / 10 = 20% studio fill rate
        self.assertEqual(d1['studio_fill_rate_percent'], 20.0)
        self.assertEqual(len(d1['template_breakdown']), 1)
        self.assertEqual(d1['template_breakdown'][0]['fill_rate_percent'], 20.0)

        # 2. Test staff performance endpoint
        staff_view = StaffPerformanceAnalyticsView.as_view()
        req2 = self.factory.get('/api/v1/retention/analytics/staff-performance/?days=30')
        req2.tenant = self.tenant
        force_authenticate(req2, user=self.owner)
        resp2 = staff_view(req2)

        self.assertEqual(resp2.status_code, 200)
        d2 = resp2.data
        self.assertIn('staff_performance', d2)
        trainer_row = next((r for r in d2['staff_performance'] if r['staff_id'] == str(self.trainer.id)), None)
        self.assertIsNotNone(trainer_row)
        self.assertEqual(trainer_row['classes_taught'], 1)
        self.assertEqual(trainer_row['total_attendees_served'], 2)
        self.assertEqual(trainer_row['assigned_clients_count'], 1)
        self.assertEqual(trainer_row['assigned_clients_retention_rate_percent'], 100.0)

    def test_task6_at_risk_summary_and_ai_risk_analysis_endpoint(self):
        """
        Verify AtRiskSummaryView, analyze-risk action on ClientRetentionMetricsViewSet,
        and tenant isolation enforcement.
        """
        now = timezone.now()

        # Set up an at-risk client with failed payment
        Payment.objects.create(
            tenant=self.tenant,
            client=self.client_risk,
            amount=Decimal('50.00'),
            type='drop_in',
            status='failed',
            idempotency_key=str(uuid.uuid4())
        )
        Package.objects.create(
            tenant=self.tenant,
            client=self.client_risk,
            package_type=self.pkg_type_monthly,
            price=Decimal('120.00'),
            credits_remaining=10,
            expires_at=now + timedelta(days=15),
            status='active'
        )
        # Add past booking 35 days ago so client is HIGH risk (inactivity + failed payment)
        session_past = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            start_at=now - timedelta(days=35),
            end_at=now - timedelta(days=35, minutes=-60),
            capacity=15
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_risk,
            session=session_past,
            status='attended',
            checked_in_at=now - timedelta(days=35)
        )
        self.client_risk.lifecycle_status = ClientLifecycleStatus.AT_RISK
        self.client_risk.save()

        RetentionMetricsService.recalculate_for_tenant(str(self.tenant.id))
        metric = ClientRetentionMetrics.objects.get(client=self.client_risk)

        # 1. Test GET /api/v1/retention/at-risk/summary/
        summary_view = AtRiskSummaryView.as_view()
        req_summary = self.factory.get('/api/v1/retention/at-risk/summary/')
        req_summary.tenant = self.tenant
        force_authenticate(req_summary, user=self.owner)
        resp_summary = summary_view(req_summary)

        self.assertEqual(resp_summary.status_code, 200)
        s_data = resp_summary.data
        self.assertGreaterEqual(s_data['total_at_risk_clients_count'], 1)
        self.assertGreater(s_data['total_at_risk_revenue'], 0)
        self.assertIn('risk_level_breakdown', s_data)
        self.assertIn('top_risk_factors', s_data)

        # 2. Test POST /api/v1/retention/metrics/<pk>/analyze-risk/
        metrics_view = ClientRetentionMetricsViewSet.as_view({'post': 'analyze_risk'})
        req_analyze = self.factory.post(f'/api/v1/retention/metrics/{metric.id}/analyze-risk/')
        req_analyze.tenant = self.tenant
        force_authenticate(req_analyze, user=self.owner)
        resp_analyze = metrics_view(req_analyze, pk=str(metric.id))

        self.assertEqual(resp_analyze.status_code, 200)
        ai_data = resp_analyze.data
        self.assertIn('ai_risk_summary', ai_data)
        self.assertIn('recommended_action', ai_data)
        self.assertIn('predictive_churn_probability', ai_data)
        self.assertEqual(ai_data['primary_churn_driver'], "Billing Failure")

        # Verify saved to database
        metric.refresh_from_db()
        self.assertTrue(metric.ai_risk_summary)
        self.assertIsNotNone(metric.ai_evaluated_at)

        # 3. Test Tenant Isolation for analyze-risk
        other_tenant = Tenant.objects.create(name="Competitor Gym", subdomain="competitor-gym")
        other_client = User.objects.create_user(
            email="competitor@gym.com", password="pass", role=UserRole.CLIENT, tenant=other_tenant
        )
        other_metric = ClientRetentionMetrics.objects.create(
            tenant=other_tenant,
            client=other_client,
            churn_risk_score=80,
            risk_level=ChurnRiskLevel.CRITICAL
        )

        # Attempt to access other_tenant's metric while authenticated on self.tenant
        req_leak = self.factory.post(f'/api/v1/retention/metrics/{other_metric.id}/analyze-risk/')
        req_leak.tenant = self.tenant
        force_authenticate(req_leak, user=self.owner)
        resp_leak = metrics_view(req_leak, pk=str(other_metric.id))
        self.assertEqual(resp_leak.status_code, 404)
