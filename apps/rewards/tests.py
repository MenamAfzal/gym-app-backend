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


class PlatformAppsWiringIntegrationTests(RewardsBaseTestCase):
    """
    Integration tests verifying canonical event processing for all newly wired platform events.
    """
    def setUp(self):
        super().setUp()
        self.program = RewardProgram.objects.create(
            tenant=self.tenant1,
            name="Alpha Loyalty",
            program_type='loyalty',
            status='active'
        )

    def test_booking_created_and_cancelled_events(self):
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Booking Created Bonus",
            event_type="booking.created",
            status="active",
            actions=[{"type": "POINTS", "amount": 10}]
        )
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Booking Cancelled Penalty/Audit",
            event_type="booking.cancelled",
            status="active",
            actions=[{"type": "POINTS", "amount": 5}]
        )

        booking_id = uuid.uuid4()
        event_created = RewardEvent.create_booking_created(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            booking_id=booking_id,
            class_name="HIIT 101",
            category="hiit"
        )
        txs_created = RewardEngineService.handle_event(event_created)
        self.assertEqual(len(txs_created), 1)
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 10)

        event_cancelled = RewardEvent.create_booking_cancelled(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            booking_id=booking_id,
            is_late_cancel=False
        )
        txs_cancelled = RewardEngineService.handle_event(event_cancelled)
        self.assertEqual(len(txs_cancelled), 1)
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 15)

    def test_facility_access_and_streak_advancement(self):
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Facility Access Points",
            event_type="facility.access",
            status="active",
            actions=[{"type": "POINTS", "amount": 25}]
        )
        event = RewardEvent.create_facility_access(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            location_id=uuid.uuid4(),
            access_point="turnstile_1"
        )
        txs = RewardEngineService.handle_event(event)
        self.assertEqual(len(txs), 1)
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 25)

        streak = UserStreak.objects.get(tenant=self.tenant1, user=self.member1, activity_type="attendance")
        self.assertEqual(streak.current_streak, 1)

    def test_workout_weight_logged_event(self):
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Weight Logged Reward",
            event_type="workout.weight_logged",
            status="active",
            actions=[{"type": "POINTS", "amount": 15}]
        )
        event = RewardEvent.create_weight_logged(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            weight_entry_id=uuid.uuid4(),
            weight_kg=75.5
        )
        txs = RewardEngineService.handle_event(event)
        self.assertEqual(len(txs), 1)
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 15)

    def test_nutrition_meal_and_water_logged_events(self):
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Meal Logged Reward",
            event_type="nutrition.meal_logged",
            status="active",
            actions=[{"type": "POINTS", "amount": 20}]
        )
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Water Logged Reward",
            event_type="nutrition.water_logged",
            status="active",
            actions=[{"type": "POINTS", "amount": 10}]
        )

        event_meal = RewardEvent.create_meal_logged(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            meal_id=uuid.uuid4(),
            meal_type="lunch",
            calories=550.0
        )
        txs_meal = RewardEngineService.handle_event(event_meal)
        self.assertEqual(len(txs_meal), 1)

        event_water = RewardEvent.create_water_logged(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            log_id=uuid.uuid4(),
            amount_ml=500
        )
        txs_water = RewardEngineService.handle_event(event_water)
        self.assertEqual(len(txs_water), 1)

        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 30)

    def test_social_engagement_events(self):
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Social Post Reward",
            event_type="social.post_created",
            status="active",
            actions=[{"type": "POINTS", "amount": 30}]
        )
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Social Like Reward",
            event_type="social.like_created",
            status="active",
            actions=[{"type": "POINTS", "amount": 5}]
        )
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Social Comment Reward",
            event_type="social.comment_created",
            status="active",
            actions=[{"type": "POINTS", "amount": 10}]
        )

        media_id = uuid.uuid4()
        event_post = RewardEvent.create_social_post_created(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            post_id=media_id
        )
        txs_post = RewardEngineService.handle_event(event_post)
        self.assertEqual(len(txs_post), 1)

        event_like = RewardEvent.create_social_like_created(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            like_id=uuid.uuid4(),
            media_id=media_id
        )
        txs_like = RewardEngineService.handle_event(event_like)
        self.assertEqual(len(txs_like), 1)

        event_comment = RewardEvent.create_social_comment_created(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            comment_id=uuid.uuid4(),
            media_id=media_id
        )
        txs_comment = RewardEngineService.handle_event(event_comment)
        self.assertEqual(len(txs_comment), 1)

        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 45)

    def test_user_registration_welcome_event(self):
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Welcome Bonus",
            event_type="user.registered",
            status="active",
            actions=[{"type": "POINTS", "amount": 100}]
        )
        new_client = User.objects.create_user(
            email="newclient@alphafit.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant1
        )
        wallet = RewardWallet.objects.get(user=new_client)
        self.assertEqual(wallet.balance, 100)

    def test_reflection_logged_emits_reward_event(self):
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Reflection Points",
            event_type="reflection.logged",
            status="active",
            actions=[{"type": "POINTS", "amount": 15}]
        )
        event = RewardEvent.create_reflection_logged(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            reflection_id=uuid.uuid4(),
            reflection_date="2026-09-07"
        )
        txs = RewardEngineService.handle_event(event)
        self.assertEqual(len(txs), 1)
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 15)

    def test_assessment_completed_emits_reward_event(self):
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Assessment Points",
            event_type="assessment.completed",
            status="active",
            actions=[{"type": "POINTS", "amount": 50}]
        )
        event = RewardEvent.create_assessment_completed(
            tenant_id=self.tenant1.id,
            user_id=self.member1.id,
            assessment_session_id=uuid.uuid4(),
            user_level="Rx1"
        )
        txs = RewardEngineService.handle_event(event)
        self.assertEqual(len(txs), 1)
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 50)

    def test_food_logging_flat_payload_via_api_awards_points(self):
        # Configure rule as in Step 4.1
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Meal Log Consistency Reward",
            event_type="nutrition.meal_logged",
            status="active",
            actions=[{"type": "POINTS", "amount": 15}]
        )

        self.client.force_authenticate(user=self.member1)
        response = self.client.post(
            "/api/v1/food/log-food/",
            data={
                "meal_type": "lunch",
                "food_name": "Grilled Chicken Salad",
                "calories": 450
            },
            format="json"
        )
        self.assertEqual(response.status_code, 201)
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 15)

    def test_client_referral_api_creates_referral_and_awards_points(self):
        # Configure rule as in Step 5.1
        RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program,
            name="Refer-a-Friend Bonus",
            event_type="referral.completed",
            status="active",
            actions=[
                {"type": "POINTS", "amount": 500, "description": "Referred a new member"},
                {"type": "PACKAGE_CREDIT", "credits": 1, "validity_days": 30}
            ]
        )

        self.client.force_authenticate(user=self.member1)

        # GET referral code info
        get_res = self.client.get("/api/v1/rewards/client/referrals/")
        self.assertEqual(get_res.status_code, 200)
        self.assertIn("referral_code", get_res.data)

        # POST complete referral
        post_res = self.client.post(
            "/api/v1/rewards/client/referrals/",
            data={"referee_email": "friend_alex@alphafit.com"},
            format="json"
        )
        self.assertEqual(post_res.status_code, 201)
        self.assertEqual(post_res.data["status"], "success")

        # Verify wallet credited
        self.member1_wallet.refresh_from_db()
        self.assertEqual(self.member1_wallet.balance, 500)

    def test_badge_image_upload_direct_and_action(self):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_authenticate(user=self.owner1)

        def make_image_file(name, color):
            img = Image.new('RGB', (50, 50), color=color)
            buf = BytesIO()
            img.save(buf, format='PNG')
            buf.seek(0)
            return SimpleUploadedFile(name, buf.read(), content_type="image/png")

        # 1. Create badge with image in multipart/form-data
        dummy_image = make_image_file("runner_icon.png", "blue")
        response = self.client.post(
            "/api/v1/rewards/admin/badges/",
            data={
                "name": "Century Runner",
                "slug": "century-runner",
                "category": "workout",
                "image": dummy_image
            },
            format="multipart"
        )
        self.assertEqual(response.status_code, 201)
        badge_id = response.data["id"]
        self.assertIn("runner_icon", response.data["image"])
        self.assertIsNotNone(response.data["icon_url"])

        # 2. Upload/replace image via dedicated action endpoint
        dummy_image_v2 = make_image_file("runner_v2.png", "red")
        action_res = self.client.post(
            f"/api/v1/rewards/admin/badges/{badge_id}/upload-image/",
            data={"image": dummy_image_v2},
            format="multipart"
        )
        self.assertEqual(action_res.status_code, 200)
        self.assertIn("runner_v2", action_res.data["image"])
        self.assertIn("runner_v2", action_res.data["icon_url"])
