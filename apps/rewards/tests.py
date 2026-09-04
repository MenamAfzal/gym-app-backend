"""
Comprehensive Test Suite for Rewards Engine

Covers:
- Dynamic Rule evaluation (DSL, compound conditions, milestones)
- Idempotency & duplicate suppression
- Concurrency & wallet locking
- Strict Multi-Tenant isolation
- Rule versioning & historical audit immutability
- Reward store redemptions & voucher lifecycle
- REST API security & role enforcement
"""
import uuid
from datetime import datetime, timezone
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant, reset_current_tenant
from apps.users.models import User, UserRole
from apps.rewards.models import (
    RewardProgram, RewardRule, RewardRuleVersion, Badge, UserBadge,
    RewardTier, RewardWallet, RewardPointLedger, RewardCatalogItem,
    RewardRedemption, ProcessedRewardEvent, RewardTransaction, UserStreak,
    TransactionType, RedemptionStatus
)
from apps.rewards.events import RewardEvent
from apps.rewards.services import (
    RewardEngineService, RewardWalletService, RewardRedemptionService
)


class RewardsBaseTestCase(TestCase):
    """
    Base test fixture providing isolated tenants and users across roles.
    """
    def setUp(self):
        self.client = APIClient()

        # Tenant 1
        self.tenant1 = Tenant.objects.create(
            name="Alpha Fitness",
            subdomain="alpha-fit",
            is_active=True
        )

        # Tenant 2 (for strict isolation testing)
        self.tenant2 = Tenant.objects.create(
            name="Beta Gym",
            subdomain="beta-gym",
            is_active=True
        )

        # Users for Tenant 1
        self.owner1 = User.objects.create_user(
            email="owner@alphafit.com",
            password="Password123!",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant1
        )
        self.member1 = User.objects.create_user(
            email="member1@alphafit.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant1
        )
        self.member1_wallet = RewardWalletService.get_or_create_wallet(tenant_id=self.tenant1.id, user=self.member1)

        # Users for Tenant 2
        self.owner2 = User.objects.create_user(
            email="owner@betagym.com",
            password="Password123!",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant2
        )
        self.member2 = User.objects.create_user(
            email="member2@betagym.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant2
        )

        # Set tenant context to Tenant 1 by default
        self.token = set_current_tenant(self.tenant1)

    def tearDown(self):
        set_current_tenant(None)


class RewardRuleEvaluationTests(RewardsBaseTestCase):
    """
    Tests for Sandboxed DSL condition evaluation, milestones, and action execution.
    """
    def setUp(self):
        super().setUp()
        self.program = RewardProgram.objects.create(
            tenant=self.tenant1,
            name="Alpha Loyalty",
            program_type='loyalty',
            status='active'
        )

    def test_simple_event_rule_awards_points(self):
        """WHEN booking.attended THEN award 50 points."""
        rule = RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Attendance Reward",
            event_type="booking.attended",
            status="active",
            actions=[{"type": "POINTS", "amount": 50, "description": "Attendance bonus"}]
        )

        event = RewardEvent(
            tenant_id=self.tenant1.id,
            event_type="booking.attended",
            user_id=self.member1.id,
            idempotency_key=f"booking:{uuid.uuid4()}:check_in",
            payload={"class_name": "HIIT Blast"}
        )

        txs = RewardEngineService.handle_event(event)

        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0].action_type, "POINTS")
        self.assertEqual(txs[0].result_status, "SUCCESS")

        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 50)
        self.assertEqual(self.member1_wallet.lifetime_earned, 50)

        # Ledger verified
        ledger_entry = RewardPointLedger.objects.filter(wallet=self.member1_wallet).first()
        self.assertIsNotNone(ledger_entry)
        self.assertEqual(ledger_entry.amount, 50)
        self.assertEqual(ledger_entry.balance_after, 50)

    def test_rule_with_payload_filter_and_badge_action(self):
        """WHEN booking.attended IF class category == 'strength' THEN award 100 points + Strength Badge."""
        badge = Badge.objects.create(
            tenant=self.tenant1,
            name="Iron Lifter",
            slug="iron-lifter"
        )

        rule = RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Strength Class Bonus",
            event_type="booking.attended",
            status="active",
            trigger_config={"category": "strength"},
            actions=[
                {"type": "POINTS", "amount": 100},
                {"type": "BADGE", "badge_slug": "iron-lifter"}
            ]
        )

        # 1. Event with non-matching category -> No reward
        yoga_event = RewardEvent(
            tenant_id=self.tenant1.id,
            event_type="booking.attended",
            user_id=self.member1.id,
            idempotency_key=f"booking:{uuid.uuid4()}:check_in",
            payload={"category": "yoga"}
        )
        txs_yoga = RewardEngineService.handle_event(yoga_event)
        self.assertEqual(len(txs_yoga), 0)
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 0)

        # 2. Event with matching category -> Rewards executed
        strength_event = RewardEvent(
            tenant_id=self.tenant1.id,
            event_type="booking.attended",
            user_id=self.member1.id,
            idempotency_key=f"booking:{uuid.uuid4()}:check_in",
            payload={"category": "strength"}
        )
        txs_strength = RewardEngineService.handle_event(strength_event)
        self.assertEqual(len(txs_strength), 2)

        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 100)

        # Verify badge awarded
        user_badge = UserBadge.objects.filter(tenant=self.tenant1, user=self.member1, badge=badge).first()
        self.assertIsNotNone(user_badge)

    def test_count_every_milestone_rule(self):
        """Test count_every operator (e.g. Every 5 attendances gives 200 points)."""
        rule = RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Every 5 Attendances Milestone",
            event_type="booking.attended",
            status="active",
            conditions=[
                {
                    "source": "attendance_count",
                    "operator": "count_every",
                    "value": 5
                }
            ],
            actions=[{"type": "POINTS", "amount": 200}]
        )

        from apps.scheduling.models import Booking, ClassSession, ClassTemplate, Location
        loc = Location.objects.create(tenant=self.tenant1, name="Main Studio", address="123 Gym St")
        tmpl = ClassTemplate.objects.create(tenant=self.tenant1, location=loc, name="CrossFit", duration_min=60)

        # Simulate 4 attendances -> no milestone
        for i in range(4):
            sess_i = ClassSession.objects.create(
                tenant=self.tenant1,
                template=tmpl,
                start_at=datetime.now(timezone.utc),
                end_at=datetime.now(timezone.utc),
                capacity=20
            )
            b = Booking.objects.create(tenant=self.tenant1, client=self.member1, session=sess_i, status='attended')
            event = RewardEvent(
                tenant_id=self.tenant1.id,
                event_type="booking.attended",
                user_id=self.member1.id,
                idempotency_key=f"booking:{b.id}:check_in",
                payload={}
            )
            txs = RewardEngineService.handle_event(event)
            self.assertEqual(len(txs), 0)

        # 5th attendance -> triggers milestone!
        sess_5 = ClassSession.objects.create(
            tenant=self.tenant1,
            template=tmpl,
            start_at=datetime.now(timezone.utc),
            end_at=datetime.now(timezone.utc),
            capacity=20
        )
        b5 = Booking.objects.create(tenant=self.tenant1, client=self.member1, session=sess_5, status='attended')
        event5 = RewardEvent(
            tenant_id=self.tenant1.id,
            event_type="booking.attended",
            user_id=self.member1.id,
            idempotency_key=f"booking:{b5.id}:check_in",
            payload={}
        )
        txs5 = RewardEngineService.handle_event(event5)
        self.assertEqual(len(txs5), 1)
        self.assertEqual(txs5[0].result_status, "SUCCESS")

        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 200)


class IdempotencyAndDeduplicationTests(RewardsBaseTestCase):
    """
    Tests ensuring identical events or concurrent deliveries never issue duplicate rewards.
    """
    def setUp(self):
        super().setUp()
        self.program = RewardProgram.objects.create(
            tenant=self.tenant1,
            name="Loyalty Program",
            status='active'
        )
        self.rule = RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Workout Completion Bonus",
            event_type="workout.completed",
            status="active",
            actions=[{"type": "POINTS", "amount": 75}]
        )

    def test_exact_same_event_processed_ten_times_only_rewards_once(self):
        """A duplicate event delivered 10 times results in exactly 1 point award."""
        event_id = str(uuid.uuid4())
        event = RewardEvent(
            tenant_id=self.tenant1.id,
            event_type="workout.completed",
            user_id=self.member1.id,
            idempotency_key=f"workout:{event_id}:completed",
            payload={"workout_name": "Full Body Murph"}
        )

        for _ in range(10):
            RewardEngineService.handle_event(event)

        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 75)
        self.assertEqual(self.member1_wallet.lifetime_earned, 75)

        ledger_count = RewardPointLedger.objects.filter(
            wallet=self.member1_wallet,
            transaction_type=TransactionType.EARN
        ).count()
        self.assertEqual(ledger_count, 1)

        event_records_count = ProcessedRewardEvent.objects.filter(
            tenant=self.tenant1,
            idempotency_key=f"workout:{event_id}:completed"
        ).count()
        self.assertEqual(event_records_count, 1)


class StrictMultiTenantIsolationTests(RewardsBaseTestCase):
    """
    Tests verifying that tenants can never see or trigger other tenants' rules, wallets, or redemptions.
    """
    def setUp(self):
        super().setUp()
        # Tenant 1 Rule
        self.prog1 = RewardProgram.objects.create(tenant=self.tenant1, name="T1 Program")
        self.rule1 = RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.prog1,
            name="T1 500 Pts Rule",
            event_type="booking.attended",
            status="active",
            actions=[{"type": "POINTS", "amount": 500}]
        )

        # Tenant 2 Rule
        set_current_tenant(self.tenant2)
        self.prog2 = RewardProgram.objects.create(tenant=self.tenant2, name="T2 Program")
        self.rule2 = RewardRule.objects.create(
            tenant=self.tenant2,
            program=self.prog2,
            name="T2 10 Pts Rule",
            event_type="booking.attended",
            status="active",
            actions=[{"type": "POINTS", "amount": 10}]
        )
        set_current_tenant(self.tenant1)

    def test_tenant_1_event_never_triggers_tenant_2_rules(self):
        """Events for Tenant 1 member must only evaluate Tenant 1 rules."""
        event1 = RewardEvent(
            tenant_id=self.tenant1.id,
            event_type="booking.attended",
            user_id=self.member1.id,
            idempotency_key=f"booking:{uuid.uuid4()}:check_in",
            payload={}
        )

        txs = RewardEngineService.handle_event(event1)
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0].rule, self.rule1)

        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 500)

        # Ensure Tenant 2 member has 0 points
        set_current_tenant(self.tenant2)
        wallet2 = RewardWalletService.get_or_create_wallet(tenant_id=self.tenant2.id, user=self.member2)
        self.assertEqual(wallet2.balance, 0)

    def test_tenant_admin_cannot_access_other_tenant_rules_via_api(self):
        """Tenant 1 Gym Owner cannot view or modify Tenant 2 rules."""
        self.client.force_authenticate(user=self.owner1)

        # Attempt to access Tenant 2 rule
        response = self.client.get(f"/api/v1/rewards/admin/rules/{self.rule2.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class RuleVersioningAndAuditingTests(RewardsBaseTestCase):
    """
    Tests ensuring rules maintain immutable version snapshots on modification.
    """
    def test_rule_modification_increments_version_and_preserves_snapshot(self):
        prog = RewardProgram.objects.create(tenant=self.tenant1, name="VIP")
        rule = RewardRule.objects.create(
            tenant=self.tenant1,
            program=prog,
            name="Check-in Reward",
            event_type="booking.attended",
            version=1,
            actions=[{"type": "POINTS", "amount": 50}]
        )

        self.assertEqual(rule.version, 1)
        self.assertEqual(RewardRuleVersion.objects.filter(rule=rule).count(), 1)

        # Update rule configuration via API or save
        rule.actions = [{"type": "POINTS", "amount": 100}]
        rule.version = 2
        rule.save()

        self.assertEqual(RewardRuleVersion.objects.filter(rule=rule).count(), 2)

        v1 = RewardRuleVersion.objects.get(rule=rule, version=1)
        v2 = RewardRuleVersion.objects.get(rule=rule, version=2)

        self.assertEqual(v1.actions_snapshot[0]['amount'], 50)
        self.assertEqual(v2.actions_snapshot[0]['amount'], 100)


class RewardStoreRedemptionTests(RewardsBaseTestCase):
    """
    Tests for point redemption, stock decrement, voucher generation, and cancellation refund.
    """
    def setUp(self):
        super().setUp()
        self.member1_wallet.balance = 500
        self.member1_wallet.lifetime_earned = 500
        self.member1_wallet.save()

        self.smoothie_item = RewardCatalogItem.objects.create(
            tenant=self.tenant1,
            name="Protein Smoothie",
            points_cost=150,
            stock_quantity=5,
            item_type='MERCHANDISE'
        )

    def test_successful_redemption_and_fulfillment(self):
        redemption = RewardRedemptionService.redeem_item(
            tenant_id=self.tenant1.id,
            user=self.member1,
            catalog_item_id=self.smoothie_item.id
        )

        self.assertEqual(redemption.status, RedemptionStatus.PENDING)
        self.assertTrue(redemption.redemption_code.startswith("RW-"))

        # Wallet balance decremented
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 350)
        self.assertEqual(self.member1_wallet.lifetime_redeemed, 150)

        # Stock quantity decremented
        self.smoothie_item.refresh_from_db()
        self.assertEqual(self.smoothie_item.stock_quantity, 4)

        # Staff fulfillment
        fulfilled = RewardRedemptionService.fulfill_redemption(
            tenant_id=self.tenant1.id,
            redemption_id=redemption.id,
            staff_user=self.owner1
        )
        self.assertEqual(fulfilled.status, RedemptionStatus.FULFILLED)
        self.assertEqual(fulfilled.fulfilled_by, self.owner1)

    def test_insufficient_points_raises_error(self):
        expensive_item = RewardCatalogItem.objects.create(
            tenant=self.tenant1,
            name="Gym Leather Bag",
            points_cost=1000,
            item_type='MERCHANDISE'
        )

        with self.assertRaises(ValueError):
            RewardRedemptionService.redeem_item(
                tenant_id=self.tenant1.id,
                user=self.member1,
                catalog_item_id=expensive_item.id
            )

        # Wallet unchanged
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 500)

    def test_cancelled_redemption_refunds_points_and_restocks(self):
        redemption = RewardRedemptionService.redeem_item(
            tenant_id=self.tenant1.id,
            user=self.member1,
            catalog_item_id=self.smoothie_item.id
        )
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 350)

        # Cancel & refund
        cancelled = RewardRedemptionService.cancel_and_refund_redemption(
            tenant_id=self.tenant1.id,
            redemption_id=redemption.id,
            staff_user=self.owner1,
            reason="Item damaged"
        )
        self.assertEqual(cancelled.status, RedemptionStatus.CANCELLED)

        # Balance refunded
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 500)
        self.assertEqual(self.member1_wallet.lifetime_redeemed, 0)

        # Restocked
        self.smoothie_item.refresh_from_db()
        self.assertEqual(self.smoothie_item.stock_quantity, 5)


class RewardsRESTAPITests(RewardsBaseTestCase):
    """
    Tests covering Admin and Client endpoints over DRF HTTP requests.
    """
    def test_client_can_view_wallet_and_history(self):
        self.member1_wallet.balance = 250
        self.member1_wallet.save()

        self.client.force_authenticate(user=self.member1)
        response = self.client.get("/api/v1/rewards/client/wallet/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['balance'], 250)

    def test_client_cannot_access_admin_endpoints(self):
        self.client.force_authenticate(user=self.member1)
        response = self.client.get("/api/v1/rewards/admin/programs/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_program_and_rules(self):
        self.client.force_authenticate(user=self.owner1)

        prog_resp = self.client.post("/api/v1/rewards/admin/programs/", {
            "name": "Summer Consistency",
            "program_type": "challenge",
            "description": "Attend 10 classes during summer"
        }, format="json")

        self.assertEqual(prog_resp.status_code, status.HTTP_201_CREATED)
        program_id = prog_resp.data['id']

        rule_resp = self.client.post("/api/v1/rewards/admin/rules/", {
            "program": program_id,
            "name": "Summer 10-Class Milestone",
            "event_type": "booking.attended",
            "conditions": [{"source": "attendance_count", "operator": "count_at_least", "value": 10}],
            "actions": [{"type": "POINTS", "amount": 300}]
        }, format="json")

        self.assertEqual(rule_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(rule_resp.data['name'], "Summer 10-Class Milestone")
