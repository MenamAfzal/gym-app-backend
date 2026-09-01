"""
Reward Engine Services

Core business logic for event processing, rule evaluation, wallet management,
and store redemptions.
"""
import logging
import secrets
from typing import List, Optional, Dict, Any
from django.db import transaction, IntegrityError
from django.utils import timezone
from apps.core.tenants.context import bypass_tenant_isolation
from apps.users.models import User
from apps.rewards.events import RewardEvent
from apps.rewards.models import (
    RewardRule, RewardRuleVersion, ProcessedRewardEvent, RewardTransaction,
    RewardWallet, RewardPointLedger, RewardCatalogItem, RewardRedemption,
    UserStreak, TransactionType, RedemptionStatus, ExecutionStatus
)
from apps.rewards.dsl import RuleConditionEvaluator
from apps.rewards.actions import ActionHandlerRegistry

logger = logging.getLogger(__name__)


class RewardEngineService:
    """
    Ingests canonical RewardEvents, evaluates active tenant rules, and triggers actions.
    """

    @classmethod
    def handle_event(cls, event: RewardEvent) -> List[RewardTransaction]:
        """
        Main entry point for platform events. Guaranteed to be idempotent and concurrency-safe.
        """
        tenant_id = event.tenant_id
        user_id = event.user_id

        with bypass_tenant_isolation():
            # Resolve user
            user = User.objects.filter(id=user_id).first()
            if not user:
                logger.warning(f"RewardEngine: User {user_id} not found.")
                return []

            # 1. Update activity streaks if applicable
            cls._update_user_streak(tenant_id, user, event.event_type)

            # 2. Ingest event inside atomic block with idempotency check
            transactions_created: List[RewardTransaction] = []

            with transaction.atomic():
                # Check or create ProcessedRewardEvent
                event_record, created = ProcessedRewardEvent.objects.get_or_create(
                    tenant_id=tenant_id,
                    event_type=event.event_type,
                    idempotency_key=event.idempotency_key,
                    defaults={
                        'user': user,
                        'occurred_at': event.occurred_at,
                        'payload': event.payload,
                        'status': 'PROCESSED'
                    }
                )

                if not created:
                    # Idempotent replay: return already generated transactions
                    logger.info(f"RewardEngine: Idempotent duplicate event suppressed: {event.idempotency_key}")
                    return list(event_record.transactions.all())

                # 3. Query active rules for this tenant & event_type
                active_rules = RewardRule.objects.filter(
                    tenant_id=tenant_id,
                    event_type=event.event_type,
                    status='active',
                    program__is_active=True
                ).order_by('-priority', 'created_at')

                if not active_rules.exists():
                    event_record.status = 'NO_RULES_MATCHED'
                    event_record.save(update_fields=['status'])
                    return []

                # 4. Evaluate each rule
                for rule in active_rules:
                    # Check user lifetime execution cap
                    if rule.max_executions_per_user is not None:
                        user_exec_count = RewardTransaction.objects.filter(
                            tenant_id=tenant_id,
                            user=user,
                            rule=rule,
                            result_status=ExecutionStatus.SUCCESS
                        ).count()
                        if user_exec_count >= rule.max_executions_per_user:
                            continue

                    # Evaluate DSL conditions
                    eval_result = RuleConditionEvaluator.evaluate(rule, event.payload, user)
                    if not eval_result.matched:
                        continue

                    milestone_key = eval_result.milestone_key

                    # Milestone deduplication check
                    if milestone_key:
                        already_awarded = RewardTransaction.objects.filter(
                            tenant_id=tenant_id,
                            rule=rule,
                            user=user,
                            milestone_key=milestone_key,
                            result_status=ExecutionStatus.SUCCESS
                        ).exists()
                        if already_awarded:
                            continue

                    # 5. Execute rule actions
                    rule_snapshot = {
                        'rule_id': str(rule.id),
                        'name': rule.name,
                        'version': rule.version,
                        'trigger_config': rule.trigger_config,
                        'conditions': rule.conditions,
                        'actions': rule.actions
                    }

                    for action in rule.actions:
                        action_type = action.get('type', 'UNKNOWN')

                        # Create transaction record container
                        tx_record = RewardTransaction(
                            tenant_id=tenant_id,
                            user=user,
                            rule=rule,
                            rule_version=rule.version,
                            rule_config_snapshot=rule_snapshot,
                            event_record=event_record,
                            action_type=action_type,
                            action_payload=action,
                            milestone_key=milestone_key
                        )

                        try:
                            action_result = ActionHandlerRegistry.execute_action(
                                action=action,
                                user=user,
                                tenant_id=tenant_id,
                                rule=rule,
                                transaction_record=tx_record
                            )

                            tx_record.result_status = ExecutionStatus.SUCCESS if action_result.success else ExecutionStatus.FAILED
                            tx_record.result_data = action_result.result_data
                            if action_result.error:
                                tx_record.result_data['error'] = action_result.error

                            tx_record.save()
                            transactions_created.append(tx_record)

                        except Exception as ex:
                            logger.exception(f"RewardEngine: Failed executing action {action_type} on rule {rule.id}: {ex}")
                            tx_record.result_status = ExecutionStatus.FAILED
                            tx_record.result_data = {'error': str(ex)}
                            tx_record.save()
                            transactions_created.append(tx_record)

            return transactions_created

    @classmethod
    def _update_user_streak(cls, tenant_id, user: User, event_type: str):
        """
        Maintains consecutive activity streaks.
        """
        activity_type = None
        if event_type in ['booking.attended', 'facility.access']:
            activity_type = 'attendance'
        elif event_type == 'workout.completed':
            activity_type = 'workout'
        elif event_type.startswith('reflection.'):
            activity_type = 'reflection'

        if not activity_type:
            return

        today = timezone.localdate()

        streak, _ = UserStreak.objects.get_or_create(
            tenant_id=tenant_id,
            user=user,
            activity_type=activity_type,
            defaults={'current_streak': 0, 'longest_streak': 0, 'last_activity_date': None}
        )

        if streak.last_activity_date == today:
            return

        yesterday = today - timezone.timedelta(days=1)
        if streak.last_activity_date == yesterday:
            streak.current_streak += 1
        else:
            streak.current_streak = 1

        if streak.current_streak > streak.longest_streak:
            streak.longest_streak = streak.current_streak

        streak.last_activity_date = today
        streak.save()


class RewardWalletService:
    """
    Manages balances, ledger adjustments, and summary stats.
    """

    @classmethod
    def get_or_create_wallet(cls, tenant_id, user: User) -> RewardWallet:
        with bypass_tenant_isolation():
            wallet, _ = RewardWallet.objects.get_or_create(
                tenant_id=tenant_id,
                user=user,
                defaults={'balance': 0, 'lifetime_earned': 0, 'lifetime_redeemed': 0}
            )
            return wallet

    @classmethod
    def adjust_points(cls, tenant_id, user: User, amount: int, reason: str, admin_user: Optional[User] = None) -> RewardWallet:
        """
        Manual staff adjustment of points (can be positive or negative).
        """
        with bypass_tenant_isolation():
            with transaction.atomic():
                wallet, _ = RewardWallet.objects.select_for_update().get_or_create(
                    tenant_id=tenant_id,
                    user=user,
                    defaults={'balance': 0, 'lifetime_earned': 0, 'lifetime_redeemed': 0}
                )

                wallet.balance += amount
                if wallet.balance < 0:
                    wallet.balance = 0

                if amount > 0:
                    wallet.lifetime_earned += amount
                wallet.save()

                RewardPointLedger.objects.create(
                    tenant_id=tenant_id,
                    wallet=wallet,
                    user=user,
                    amount=amount,
                    balance_after=wallet.balance,
                    transaction_type=TransactionType.ADJUSTMENT,
                    description=f"Manual Adjustment by {admin_user.email if admin_user else 'Admin'}: {reason}"
                )

                return wallet


class RewardRedemptionService:
    """
    Handles store redemptions, vouchers, and staff fulfillment.
    """

    @classmethod
    def redeem_item(cls, tenant_id, user: User, catalog_item_id, notes: str = "") -> RewardRedemption:
        """
        Redeems points for an item in the reward store.
        """
        with bypass_tenant_isolation():
            with transaction.atomic():
                item = RewardCatalogItem.objects.select_for_update().filter(
                    tenant_id=tenant_id,
                    id=catalog_item_id,
                    is_active=True
                ).first()

                if not item:
                    raise ValueError("Reward catalog item not found or inactive.")

                if item.stock_quantity is not None:
                    if item.stock_quantity <= 0:
                        raise ValueError("Reward item is out of stock.")
                    item.stock_quantity -= 1
                    item.save(update_fields=['stock_quantity'])

                # Lock wallet
                wallet, _ = RewardWallet.objects.select_for_update().get_or_create(
                    tenant_id=tenant_id,
                    user=user,
                    defaults={'balance': 0, 'lifetime_earned': 0, 'lifetime_redeemed': 0}
                )

                if wallet.balance < item.points_cost:
                    raise ValueError(f"Insufficient points. Required: {item.points_cost}, Available: {wallet.balance}")

                # Deduct points
                wallet.balance -= item.points_cost
                wallet.lifetime_redeemed += item.points_cost
                wallet.save()

                # Generate unique voucher code
                voucher_code = f"RW-{secrets.token_hex(4).upper()}"

                redemption = RewardRedemption.objects.create(
                    tenant_id=tenant_id,
                    user=user,
                    catalog_item=item,
                    points_spent=item.points_cost,
                    status=RedemptionStatus.PENDING,
                    redemption_code=voucher_code,
                    notes=notes
                )

                # Record Ledger Entry
                RewardPointLedger.objects.create(
                    tenant_id=tenant_id,
                    wallet=wallet,
                    user=user,
                    amount=-item.points_cost,
                    balance_after=wallet.balance,
                    transaction_type=TransactionType.REDEEM,
                    description=f"Redeemed reward: {item.name} ({voucher_code})",
                    redemption=redemption
                )

                # If catalog item is linked to a package type, automatically grant the package credits!
                if item.package_type_id:
                    from apps.scheduling.models import Package
                    Package.objects.create(
                        tenant_id=tenant_id,
                        client=user,
                        package_type=item.package_type,
                        credits_remaining=item.package_type.credit_count,
                        expires_at=timezone.now() + timezone.timedelta(days=item.package_type.validity_days),
                        status='active',
                        is_complimentary=True,
                        price=0.00
                    )

                return redemption

    @classmethod
    def fulfill_redemption(cls, tenant_id, redemption_id, staff_user: User) -> RewardRedemption:
        with bypass_tenant_isolation():
            with transaction.atomic():
                redemption = RewardRedemption.objects.select_for_update().filter(
                    tenant_id=tenant_id,
                    id=redemption_id
                ).first()

                if not redemption:
                    raise ValueError("Redemption record not found.")

                if redemption.status == RedemptionStatus.FULFILLED:
                    return redemption

                redemption.status = RedemptionStatus.FULFILLED
                redemption.fulfilled_by = staff_user
                redemption.fulfilled_at = timezone.now()
                redemption.save()

                return redemption

    @classmethod
    def cancel_and_refund_redemption(cls, tenant_id, redemption_id, staff_user: Optional[User] = None, reason: str = "") -> RewardRedemption:
        with bypass_tenant_isolation():
            with transaction.atomic():
                redemption = RewardRedemption.objects.select_for_update().filter(
                    tenant_id=tenant_id,
                    id=redemption_id
                ).first()

                if not redemption:
                    raise ValueError("Redemption record not found.")

                if redemption.status == RedemptionStatus.CANCELLED:
                    return redemption

                redemption.status = RedemptionStatus.CANCELLED
                redemption.notes = f"{redemption.notes}\nCancelled by {staff_user.email if staff_user else 'System'}: {reason}".strip()
                redemption.save()

                # Refund points to wallet
                if redemption.points_spent > 0:
                    wallet = RewardWallet.objects.select_for_update().get(
                        tenant_id=tenant_id,
                        user=redemption.user
                    )
                    wallet.balance += redemption.points_spent
                    wallet.lifetime_redeemed -= redemption.points_spent
                    wallet.save()

                    RewardPointLedger.objects.create(
                        tenant_id=tenant_id,
                        wallet=wallet,
                        user=redemption.user,
                        amount=redemption.points_spent,
                        balance_after=wallet.balance,
                        transaction_type=TransactionType.REVERSAL,
                        description=f"Refund for cancelled redemption {redemption.redemption_code}",
                        redemption=redemption
                    )

                # Restock inventory item if applicable
                if redemption.catalog_item.stock_quantity is not None:
                    redemption.catalog_item.stock_quantity += 1
                    redemption.catalog_item.save(update_fields=['stock_quantity'])

                return redemption
