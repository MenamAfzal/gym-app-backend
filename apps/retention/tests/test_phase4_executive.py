import datetime
import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.tenants.context import set_current_tenant, reset_current_tenant
from apps.core.tenants.models import Tenant
from apps.retention.ai_service import RetentionAIService
from apps.retention.models import (
    ChurnRiskLevel,
    ClientRetentionMetrics,
    RetentionActionType,
    RetentionCampaignActionLog,
    RetentionCampaignTrigger,
    RetentionConversionAttribution,
    RetentionTriggerType,
    TenantRetentionDailySnapshot,
    WeeklyBusinessInsight,
)
from apps.retention.tasks import generate_all_weekly_insights
from apps.retention.views import ExecutiveKPIDashboardView
from apps.scheduling.models import (
    Booking, ClassSession, ClassTemplate, Location, Package, PackageType, Payment, Room
)
from apps.users.models import ClientLifecycleStatus, User, UserRole


class Phase4ExecutiveAITestCase(TestCase):
    def setUp(self):
        # Patch external tasks
        self.waitlist_patcher = patch('apps.scheduling.tasks.process_waitlist_promotion_job.delay')
        self.mock_waitlist = self.waitlist_patcher.start()
        self.addCleanup(self.waitlist_patcher.stop)

        self.retention_patcher = patch('apps.retention.tasks.recalculate_single_client_metrics.delay')
        self.mock_retention = self.retention_patcher.start()
        self.addCleanup(self.retention_patcher.stop)

        # 1. Primary Tenant
        self.tenant = Tenant.objects.create(name="Apex Fitness Studio", subdomain="apex-p4")
        self.token = set_current_tenant(self.tenant)
        self.addCleanup(lambda: reset_current_tenant(self.token))

        # 2. Location, Room, Template
        self.location = Location.objects.create(
            tenant=self.tenant,
            name="Main Facility",
            address="500 Broadway",
            timezone="UTC"
        )
        self.room = Room.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Studio 1",
            capacity=20
        )
        self.template_spin = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Power Spin",
            duration_min=45
        )
        self.template_yoga = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Restorative Yoga",
            duration_min=60
        )

        # 3. Users
        self.owner = User.objects.create_user(
            email="owner@apexfitness.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant,
            first_name="Elena",
            last_name="Rostova"
        )
        self.client_active = User.objects.create_user(
            email="active.client@apexfitness.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            first_name="Liam",
            last_name="Neeson",
            lifecycle_status=ClientLifecycleStatus.ACTIVE
        )
        self.client_risk = User.objects.create_user(
            email="risk.client@apexfitness.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            first_name="Bruce",
            last_name="Wayne",
            lifecycle_status=ClientLifecycleStatus.AT_RISK
        )

        # 4. Secondary Tenant for Tenant Isolation Verification
        self.other_tenant = Tenant.objects.create(name="Metro Gym", subdomain="metro-p4")
        self.other_owner = User.objects.create_user(
            email="owner@metrogym.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.other_tenant,
            first_name="Clark",
            last_name="Kent"
        )

        self.factory = APIRequestFactory()

    def test_generate_weekly_business_insights_aggregation_and_fallback(self):
        """
        Verify that generate_weekly_business_insights aggregates delta WoW metrics:
        attendance, churn, at-risk revenue, and conversions, and creates WeeklyBusinessInsight.
        """
        # Fix reference date: Monday 2026-10-05
        # Previous complete week: Monday 2026-09-28 to Sunday 2026-10-04
        # Prior week: Monday 2026-09-21 to Sunday 2026-09-27
        ref_date = date(2026, 10, 5)
        w_start = date(2026, 9, 28)
        w_end = date(2026, 10, 4)

        # A. Create sessions and bookings for this week
        session_this = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template_spin,
            room=self.room,
            start_at=timezone.datetime(2026, 9, 29, 10, 0, tzinfo=datetime.timezone.utc),
            end_at=timezone.datetime(2026, 9, 29, 11, 0, tzinfo=datetime.timezone.utc),
            capacity=20
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_active,
            session=session_this,
            status='attended',
            checked_in_at=session_this.start_at
        )

        # B. Snapshots for churn rate tracking
        TenantRetentionDailySnapshot.objects.create(
            tenant=self.tenant,
            snapshot_date=w_end,
            retention_rate_monthly=Decimal('88.50'),
            churn_rate_monthly=Decimal('4.50')
        )
        TenantRetentionDailySnapshot.objects.create(
            tenant=self.tenant,
            snapshot_date=date(2026, 9, 27),
            retention_rate_monthly=Decimal('89.00'),
            churn_rate_monthly=Decimal('4.00')
        )

        # C. At-risk metrics
        ClientRetentionMetrics.objects.create(
            tenant=self.tenant,
            client=self.client_risk,
            churn_risk_score=80,
            risk_level=ChurnRiskLevel.HIGH,
            estimated_monthly_value=Decimal('150.00'),
            at_risk_revenue=Decimal('150.00'),
            days_since_last_visit=28
        )

        # D. Attributed conversion revenue this week
        RetentionConversionAttribution.objects.create(
            tenant=self.tenant,
            client=self.client_active,
            attributed_revenue=Decimal('75.00'),
            conversion_event='package_purchase',
            converted_at=timezone.datetime(2026, 10, 1, 14, 0, tzinfo=datetime.timezone.utc)
        )

        # Execute insight generation
        insight = RetentionAIService.generate_weekly_business_insights(
            tenant=self.tenant,
            reference_date=ref_date,
            use_llm=False  # Deterministic synthesis for automated testing
        )

        self.assertIsNotNone(insight)
        self.assertEqual(insight.tenant, self.tenant)
        self.assertEqual(insight.week_start, w_start)
        self.assertEqual(insight.week_end, w_end)

        # Verify executive summary
        self.assertTrue(len(insight.executive_summary) > 20)
        self.assertIn("Apex Fitness Studio", self.tenant.name)

        # Verify revenue insights
        rev_data = insight.revenue_insights
        self.assertEqual(rev_data['attributed_conversion_revenue'], 75.0)
        self.assertEqual(rev_data['at_risk_revenue_total'], 150.0)

        # Verify retention insights
        ret_data = insight.retention_insights
        self.assertEqual(ret_data['classes_attended'], 1)
        self.assertEqual(ret_data['monthly_churn_rate'], 4.5)
        self.assertEqual(ret_data['high_critical_risk_clients'], 1)

        # Verify recommended actions list
        actions = insight.recommended_actions
        self.assertIsInstance(actions, list)
        self.assertGreaterEqual(len(actions), 2)
        self.assertTrue(any("high" in a.lower() or "risk" in a.lower() for a in actions))

        # Check database persistence and update idempotency
        self.assertEqual(WeeklyBusinessInsight.objects.filter(tenant=self.tenant).count(), 1)
        # Re-running updates in place without creating duplicates
        insight_re = RetentionAIService.generate_weekly_business_insights(
            tenant=self.tenant,
            reference_date=ref_date,
            use_llm=False
        )
        self.assertEqual(insight_re.id, insight.id)
        self.assertEqual(WeeklyBusinessInsight.objects.filter(tenant=self.tenant).count(), 1)

    def test_executive_kpi_dashboard_api(self):
        """
        Verify GET /api/v1/retention/dashboard/executive/ returns unified high-level
        KPIs: current_health, campaign_performance_last_30d, latest_weekly_insight, top_risk_clients.
        """
        now = timezone.now()

        # 1. Setup metrics and snapshots
        TenantRetentionDailySnapshot.objects.create(
            tenant=self.tenant,
            snapshot_date=now.date(),
            churn_rate_monthly=Decimal('3.80'),
            retention_rate_monthly=Decimal('92.10')
        )

        ClientRetentionMetrics.objects.create(
            tenant=self.tenant,
            client=self.client_risk,
            churn_risk_score=92,
            risk_level=ChurnRiskLevel.CRITICAL,
            estimated_monthly_value=Decimal('200.00'),
            at_risk_revenue=Decimal('200.00'),
            days_since_last_visit=45,
            risk_factors=["No visits in 45 days", "Failed billing"]
        )

        # 2. Setup trigger, action log, and attribution in last 30d
        trigger = RetentionCampaignTrigger.objects.create(
            tenant=self.tenant,
            name="Re-engagement Flow",
            trigger_type=RetentionTriggerType.INACTIVITY,
            trigger_value=14,
            template_body="Come back soon!"
        )
        log = RetentionCampaignActionLog.objects.create(
            tenant=self.tenant,
            client=self.client_risk,
            trigger=trigger,
            action_type=RetentionActionType.SENT,
            sent_at=now - timedelta(days=5),
            converted_at=now - timedelta(days=2)
        )
        RetentionConversionAttribution.objects.create(
            tenant=self.tenant,
            action_log=log,
            client=self.client_risk,
            attributed_revenue=Decimal('120.00'),
            conversion_event='package_purchase',
            converted_at=now - timedelta(days=2)
        )

        # 3. Setup WeeklyBusinessInsight
        w_insight = WeeklyBusinessInsight.objects.create(
            tenant=self.tenant,
            week_start=now.date() - timedelta(days=7),
            week_end=now.date() - timedelta(days=1),
            executive_summary="Weekly studio health overview.",
            revenue_insights={"attributed_conversion_revenue": 120.0},
            retention_insights={"classes_attended": 10},
            recommended_actions=["Follow up with lapsed members"],
            generated_at=now
        )

        # 4. Call API
        view = ExecutiveKPIDashboardView.as_view()
        req = self.factory.get('/api/v1/retention/dashboard/executive/')
        req.tenant = self.tenant
        force_authenticate(req, user=self.owner)
        resp = view(req)

        self.assertEqual(resp.status_code, 200)
        data = resp.data

        # Verify current_health
        self.assertIn("current_health", data)
        health = data["current_health"]
        self.assertEqual(health["total_active_members"], 1)
        self.assertEqual(health["at_risk_members_count"], 1)
        self.assertEqual(health["monthly_churn_rate_percent"], 3.8)
        self.assertEqual(health["total_at_risk_revenue"], 200.0)

        # Verify campaign_performance_last_30d
        self.assertIn("campaign_performance_last_30d", data)
        perf = data["campaign_performance_last_30d"]
        self.assertEqual(perf["period_days"], 30)
        self.assertEqual(perf["sent_interventions_count"], 1)
        self.assertEqual(perf["converted_interventions_count"], 1)
        self.assertEqual(perf["conversion_rate_percent"], 100.0)
        self.assertEqual(perf["total_attributed_revenue"], 120.0)

        # Verify latest_weekly_insight
        self.assertIn("latest_weekly_insight", data)
        self.assertIsNotNone(data["latest_weekly_insight"])
        self.assertEqual(data["latest_weekly_insight"]["executive_summary"], "Weekly studio health overview.")

        # Verify top_risk_clients
        self.assertIn("top_risk_clients", data)
        top_risk = data["top_risk_clients"]
        self.assertEqual(len(top_risk), 1)
        self.assertEqual(top_risk[0]["client_id"], str(self.client_risk.id))
        self.assertEqual(top_risk[0]["churn_risk_score"], 92)
        self.assertEqual(top_risk[0]["estimated_monthly_value"], 200.0)

    def test_executive_kpi_dashboard_tenant_isolation(self):
        """
        Verify that a gym owner from another tenant cannot see Apex Fitness Studio's data.
        """
        now = timezone.now()
        ClientRetentionMetrics.objects.create(
            tenant=self.tenant,
            client=self.client_risk,
            churn_risk_score=95,
            risk_level=ChurnRiskLevel.CRITICAL,
            at_risk_revenue=Decimal('300.00')
        )

        view = ExecutiveKPIDashboardView.as_view()
        req = self.factory.get('/api/v1/retention/dashboard/executive/')
        req.tenant = self.other_tenant
        force_authenticate(req, user=self.other_owner)
        resp = view(req)

        self.assertEqual(resp.status_code, 200)
        data = resp.data
        # For other tenant, at-risk revenue and risk count should be 0
        self.assertEqual(data["current_health"]["total_at_risk_revenue"], 0.0)
        self.assertEqual(len(data["top_risk_clients"]), 0)
        self.assertIsNone(data["latest_weekly_insight"])

    def test_celery_task_generate_all_weekly_insights(self):
        """
        Verify Celery scheduled task generate_all_weekly_insights processes active tenants.
        """
        processed = generate_all_weekly_insights()
        self.assertGreaterEqual(processed, 2)
        self.assertTrue(WeeklyBusinessInsight.objects.filter(tenant=self.tenant).exists())
