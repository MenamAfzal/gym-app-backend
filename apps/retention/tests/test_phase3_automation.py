import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.tenants.context import set_current_tenant, reset_current_tenant
from apps.core.tenants.models import Tenant
from apps.notifications.models import DeliveryRecord, NotificationInbox
from apps.retention.automation_service import RetentionAutomationService
from apps.retention.models import (
    ClientRetentionMetrics,
    RetentionActionType,
    RetentionCampaignActionLog,
    RetentionCampaignTrigger,
    RetentionChannel,
    RetentionConversionAttribution,
    RetentionTriggerType,
    SavedSegment,
)
from apps.retention.services import AttributionService, RetentionMetricsService
from apps.retention.tasks import attribute_conversion_task, evaluate_all_retention_triggers
from apps.retention.views import RetentionCampaignTriggerViewSet
from apps.scheduling.models import (
    Booking, ClassSession, ClassTemplate, Location, Package, PackageType, Payment, Room
)
from apps.users.models import ClientLifecycleStatus, User, UserRole


class Phase3RetentionAutomationTestCase(TestCase):
    def setUp(self):
        # Patch external delay tasks
        self.waitlist_patcher = patch('apps.scheduling.tasks.process_waitlist_promotion_job.delay')
        self.mock_waitlist = self.waitlist_patcher.start()
        self.addCleanup(self.waitlist_patcher.stop)

        self.retention_patcher = patch('apps.retention.tasks.recalculate_single_client_metrics.delay')
        self.mock_retention = self.retention_patcher.start()
        self.addCleanup(self.retention_patcher.stop)

        # 1. Primary Tenant
        self.tenant = Tenant.objects.create(name="Peak Performance Gym", subdomain="peak-gym-p3")
        self.token = set_current_tenant(self.tenant)
        self.addCleanup(lambda: reset_current_tenant(self.token))

        # 2. Location, Room, Template
        self.location = Location.objects.create(
            tenant=self.tenant,
            name="Downtown Studio",
            address="100 Fitness Rd",
            timezone="UTC"
        )
        self.room = Room.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Main Room",
            capacity=25
        )
        self.template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="HIIT Strength",
            duration_min=45
        )
        self.pkg_type = PackageType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="10-Class Pack",
            credit_count=10,
            validity_days=60,
            price=Decimal('150.00'),
            billing_cycle='one_time'
        )

        # 3. Users
        self.owner = User.objects.create_user(
            email="owner@peakp3.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant,
            first_name="Marcus",
            last_name="Vance"
        )
        self.client_a = User.objects.create_user(
            email="client.a@peakp3.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            first_name="Sarah",
            last_name="Connor",
            lifecycle_status=ClientLifecycleStatus.AT_RISK
        )
        self.client_b = User.objects.create_user(
            email="client.b@peakp3.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            first_name="Kyle",
            last_name="Reese",
            lifecycle_status=ClientLifecycleStatus.ACTIVE
        )

        # Secondary tenant for isolation checks
        self.other_tenant = Tenant.objects.create(name="Competitor Gym", subdomain="comp-gym-p3")
        self.other_owner = User.objects.create_user(
            email="other.owner@competitor.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.other_tenant,
            first_name="Dave",
            last_name="Miller"
        )

        self.factory = APIRequestFactory()

    def test_inactivity_milestone_trigger_and_throttling(self):
        """
        Verify that reaching exactly 14 days of inactivity fires an automated
        intervention action log, and that it is throttled to once every 30 days.
        """
        # Set client_a to exactly 14 days inactive in metrics
        ClientRetentionMetrics.objects.create(
            tenant=self.tenant,
            client=self.client_a,
            days_since_last_visit=14,
            visits_last_30d=0,
            churn_risk_score=45
        )

        # Create active 14-day inactivity trigger
        trigger = RetentionCampaignTrigger.objects.create(
            tenant=self.tenant,
            name="14-Day Inactivity Win-back",
            trigger_type=RetentionTriggerType.INACTIVITY,
            trigger_value=14,
            channel=RetentionChannel.EMAIL,
            template_subject="We miss you, {first_name}!",
            template_body="Hi {first_name}, come back to {studio_name}!",
            use_ai_personalization=False,
            is_active=True
        )

        # 1. Run evaluation - should execute and dispatch
        logs = RetentionAutomationService.evaluate_inactivity_triggers(tenant=self.tenant)
        self.assertEqual(len(logs), 1)
        action_log = logs[0]
        self.assertEqual(action_log.client, self.client_a)
        self.assertEqual(action_log.trigger, trigger)
        self.assertEqual(action_log.action_type, RetentionActionType.SENT)
        self.assertIn("Sarah", action_log.trigger.template_subject.replace("{first_name}", self.client_a.first_name))

        # Verify NotificationInbox & DeliveryRecord were created
        inbox = NotificationInbox.objects.filter(recipient=self.client_a).first()
        self.assertIsNotNone(inbox)
        self.assertIn("We miss you, Sarah!", inbox.title)
        self.assertIn("Peak Performance Gym", inbox.body)
        self.assertTrue(DeliveryRecord.objects.filter(inbox_item=inbox).exists())

        # 2. Run evaluation again immediately - must be throttled by 30-day window
        logs_second = RetentionAutomationService.evaluate_inactivity_triggers(tenant=self.tenant)
        self.assertEqual(len(logs_second), 0)
        # Total logs in database should still be strictly 1
        self.assertEqual(RetentionCampaignActionLog.objects.filter(client=self.client_a).count(), 1)

    def test_ai_personalized_winback_generation(self):
        """
        Verify that triggers with use_ai_personalization=True synthesize dynamic
        copy tailored to client risk factors and persist ai_generated_body on the action log.
        """
        metric = ClientRetentionMetrics.objects.create(
            tenant=self.tenant,
            client=self.client_a,
            days_since_last_visit=30,
            churn_risk_score=75,
            failed_payments_last_90d=1,
            risk_factors=["No visits in the last 30 days", "Recent failed payment"]
        )

        trigger_ai = RetentionCampaignTrigger.objects.create(
            tenant=self.tenant,
            name="AI High Risk Intervention",
            trigger_type=RetentionTriggerType.INACTIVITY,
            trigger_value=30,
            channel=RetentionChannel.EMAIL,
            template_subject="Important update from {studio_name}",
            template_body="Default template body",
            use_ai_personalization=True,
            is_active=True
        )

        log = RetentionAutomationService.execute_trigger(
            client=self.client_a,
            trigger=trigger_ai,
            metrics=metric
        )

        self.assertIsNotNone(log)
        self.assertEqual(log.action_type, RetentionActionType.SENT)
        self.assertTrue(len(log.ai_generated_body) > 10)
        # Verify personalized content addresses billing or attendance lapse
        self.assertTrue(
            "payment" in log.ai_generated_body.lower() or
            "30 days" in log.ai_generated_body.lower() or
            "sarah" in log.ai_generated_body.lower()
        )

    def test_failed_payment_trigger_evaluation(self):
        """
        Verify that failed payments trigger an automated notification intervention.
        """
        now = timezone.now()
        Payment.objects.create(
            tenant=self.tenant,
            client=self.client_a,
            amount=Decimal('45.00'),
            type='drop_in',
            status='failed',
            idempotency_key=str(uuid.uuid4())
        )

        trigger = RetentionCampaignTrigger.objects.create(
            tenant=self.tenant,
            name="Failed Billing Recovery",
            trigger_type=RetentionTriggerType.FAILED_PAYMENT,
            channel=RetentionChannel.EMAIL,
            template_subject="Action Required: Payment Issue",
            template_body="Hi {first_name}, please update your card on file.",
            is_active=True
        )

        logs = RetentionAutomationService.evaluate_failed_payment_triggers(tenant=self.tenant)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].client, self.client_a)
        self.assertEqual(logs[0].trigger, trigger)

        # Verify throttling (no duplicate within 7 days)
        logs_repeat = RetentionAutomationService.evaluate_failed_payment_triggers(tenant=self.tenant)
        self.assertEqual(len(logs_repeat), 0)

    def test_package_expiry_trigger_evaluation(self):
        """
        Verify that packages expiring in exactly trigger_value days dispatch a renewal reminder.
        """
        now = timezone.now()
        target_expiry = now + timedelta(days=7)

        Package.objects.create(
            tenant=self.tenant,
            client=self.client_b,
            package_type=self.pkg_type,
            price=Decimal('150.00'),
            credits_remaining=3,
            expires_at=target_expiry,
            status='active'
        )

        trigger = RetentionCampaignTrigger.objects.create(
            tenant=self.tenant,
            name="Pass Expiry 7-Day Warning",
            trigger_type=RetentionTriggerType.PACKAGE_EXPIRY,
            trigger_value=7,
            channel=RetentionChannel.EMAIL,
            template_subject="Your pass is expiring in 7 days!",
            template_body="Hi {first_name}, you have 3 credits remaining.",
            is_active=True
        )

        logs = RetentionAutomationService.evaluate_expiry_triggers(tenant=self.tenant)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].client, self.client_b)
        self.assertEqual(logs[0].trigger, trigger)

    def test_attribution_service_booking_and_payment_conversions(self):
        """
        Verify AttributionService hooks:
        1. Action log exists within 7 days.
        2. Booking checked in -> marks action log converted and creates RetentionConversionAttribution.
        3. Package purchase -> marks action log converted and attributes revenue.
        """
        now = timezone.now()
        trigger = RetentionCampaignTrigger.objects.create(
            tenant=self.tenant,
            name="Winback Offer",
            trigger_type=RetentionTriggerType.INACTIVITY,
            trigger_value=14,
            template_body="Winback body"
        )

        # 1. Simulate a sent campaign action log 2 days ago
        action_log = RetentionCampaignActionLog.objects.create(
            tenant=self.tenant,
            client=self.client_a,
            trigger=trigger,
            action_type=RetentionActionType.SENT,
            sent_at=now - timedelta(days=2)
        )
        self.assertIsNone(action_log.converted_at)

        # 2. Client attends a class booking
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            room=self.room,
            start_at=now + timedelta(hours=2),
            end_at=now + timedelta(hours=3),
            capacity=15
        )
        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client_a,
            package_type=self.pkg_type,
            price=Decimal('25.00'),
            credits_remaining=5,
            expires_at=now + timedelta(days=30),
            status='active'
        )
        booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client_a,
            session=session,
            credit_source=pkg,
            status='checked_in',
            checked_in_at=now
        )

        # Trigger attribution hook
        attr_booking = AttributionService.attribute_conversion(
            tenant=self.tenant,
            client=self.client_a,
            event_type='booking_checkin',
            related_object=booking
        )

        self.assertIsNotNone(attr_booking)
        action_log.refresh_from_db()
        self.assertIsNotNone(action_log.converted_at)
        self.assertEqual(action_log.conversion_booking, booking)
        self.assertEqual(attr_booking.action_log, action_log)
        self.assertEqual(attr_booking.booking, booking)
        self.assertEqual(attr_booking.attributed_revenue, Decimal('25.00'))  # Template price

        # 3. Client also buys a 10-Class Package ($150.00)
        action_log_pkg = RetentionCampaignActionLog.objects.create(
            tenant=self.tenant,
            client=self.client_b,
            trigger=trigger,
            action_type=RetentionActionType.SENT,
            sent_at=now - timedelta(days=1)
        )
        payment = Payment.objects.create(
            tenant=self.tenant,
            client=self.client_b,
            amount=Decimal('150.00'),
            type='package_purchase',
            status='completed',
            idempotency_key=str(uuid.uuid4())
        )

        attr_payment = AttributionService.attribute_conversion(
            tenant=self.tenant,
            client=self.client_b,
            event_type='package_purchase',
            related_object=payment
        )

        self.assertIsNotNone(attr_payment)
        action_log_pkg.refresh_from_db()
        self.assertIsNotNone(action_log_pkg.converted_at)
        self.assertEqual(attr_payment.payment, payment)
        self.assertEqual(attr_payment.attributed_revenue, Decimal('150.00'))

    def test_attribution_expired_outside_7d_window(self):
        """
        Verify that actions older than 7 days are NOT attributed to new conversions.
        """
        now = timezone.now()
        trigger = RetentionCampaignTrigger.objects.create(
            tenant=self.tenant,
            name="Old Campaign",
            trigger_type=RetentionTriggerType.INACTIVITY
        )
        # Action log sent 10 days ago (outside 7d window)
        action_log = RetentionCampaignActionLog.objects.create(
            tenant=self.tenant,
            client=self.client_a,
            trigger=trigger,
            action_type=RetentionActionType.SENT,
            sent_at=now - timedelta(days=10)
        )

        payment = Payment.objects.create(
            tenant=self.tenant,
            client=self.client_a,
            amount=Decimal('100.00'),
            type='package_purchase',
            status='completed',
            idempotency_key=str(uuid.uuid4())
        )

        attr = AttributionService.attribute_conversion(
            tenant=self.tenant,
            client=self.client_a,
            event_type='package_purchase',
            related_object=payment
        )
        self.assertIsNone(attr)
        action_log.refresh_from_db()
        self.assertIsNone(action_log.converted_at)

    def test_trigger_crud_and_performance_api(self):
        """
        Verify RetentionCampaignTriggerViewSet CRUD and /performance/ endpoint metrics.
        """
        view_list = RetentionCampaignTriggerViewSet.as_view({'get': 'list', 'post': 'create'})
        view_perf = RetentionCampaignTriggerViewSet.as_view({'get': 'performance'})

        # 1. Create a trigger via API
        payload = {
            "name": "21-Day Inactivity Warning",
            "trigger_type": "inactivity",
            "trigger_value": 21,
            "channel": "email",
            "template_subject": "We want you back, {first_name}",
            "template_body": "Come back for a special session!",
            "use_ai_personalization": True,
            "is_active": True
        }
        req_create = self.factory.post('/api/v1/retention/triggers/', payload, format='json')
        req_create.tenant = self.tenant
        force_authenticate(req_create, user=self.owner)
        resp_create = view_list(req_create)
        self.assertEqual(resp_create.status_code, 201)
        trigger_id = resp_create.data['id']
        trigger = RetentionCampaignTrigger.objects.get(id=trigger_id)
        self.assertEqual(trigger.tenant, self.tenant)

        # 2. Add mock action logs and attribution
        now = timezone.now()
        log1 = RetentionCampaignActionLog.objects.create(
            tenant=self.tenant,
            client=self.client_a,
            trigger=trigger,
            action_type=RetentionActionType.SENT,
            sent_at=now - timedelta(days=3),
            converted_at=now - timedelta(days=1)
        )
        log2 = RetentionCampaignActionLog.objects.create(
            tenant=self.tenant,
            client=self.client_b,
            trigger=trigger,
            action_type=RetentionActionType.SENT,
            sent_at=now - timedelta(days=2)
        )
        RetentionConversionAttribution.objects.create(
            tenant=self.tenant,
            action_log=log1,
            client=self.client_a,
            conversion_event='package_purchase',
            attributed_revenue=Decimal('200.00'),
            converted_at=now - timedelta(days=1)
        )

        # 3. GET /performance/
        req_perf = self.factory.get(f'/api/v1/retention/triggers/{trigger_id}/performance/')
        req_perf.tenant = self.tenant
        force_authenticate(req_perf, user=self.owner)
        resp_perf = view_perf(req_perf, pk=str(trigger_id))

        self.assertEqual(resp_perf.status_code, 200)
        p_data = resp_perf.data
        self.assertEqual(p_data['trigger_id'], str(trigger_id))
        self.assertEqual(p_data['funnel']['sent'], 2)
        self.assertEqual(p_data['funnel']['converted'], 1)
        self.assertEqual(p_data['funnel']['conversion_rate_percent'], 50.0)
        self.assertEqual(p_data['revenue']['total_attributed_revenue'], 200.0)
        self.assertEqual(p_data['revenue']['average_revenue_per_conversion'], 200.0)
        self.assertEqual(len(p_data['recent_conversions']), 1)

        # 4. Tenant Isolation Check: Competitor owner cannot access
        req_other = self.factory.get(f'/api/v1/retention/triggers/{trigger_id}/performance/')
        req_other.tenant = self.other_tenant
        force_authenticate(req_other, user=self.other_owner)
        resp_other = view_perf(req_other, pk=str(trigger_id))
        self.assertEqual(resp_other.status_code, 404)

    def test_celery_task_evaluate_all_retention_triggers(self):
        """
        Verify evaluate_all_retention_triggers runs cleanly across tenants.
        """
        RetentionCampaignTrigger.objects.create(
            tenant=self.tenant,
            name="General Inactivity",
            trigger_type=RetentionTriggerType.INACTIVITY,
            trigger_value=14,
            is_active=True
        )

        total = evaluate_all_retention_triggers()
        self.assertGreaterEqual(total, 0)
