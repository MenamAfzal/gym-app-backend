"""
Reward Engine Services

Core business logic for event processing, rule evaluation, wallet management,
and store redemptions.
"""
import logging
import secrets
from typing import List, Optional, Dict, Any
from django.db import transaction, IntegrityError
from django.core.exceptions import ValidationError
from django.utils import timezone
from apps.core.tenants.context import bypass_tenant_isolation
from apps.users.models import User, UserRole
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
                    program__status='active'
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
                    
                    # Check rolling window execution cap
                    if rule.max_executions_per_period is not None and rule.period_window_days is not None:
                        cutoff_date = timezone.now() - timezone.timedelta(days=rule.period_window_days)
                        period_exec_count = RewardTransaction.objects.filter(
                            tenant_id=tenant_id,
                            user=user,
                            rule=rule,
                            result_status=ExecutionStatus.SUCCESS,
                            created_at__gte=cutoff_date
                        ).count()
                        if period_exec_count >= rule.max_executions_per_period:
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
        elif event_type.startswith('nutrition.'):
            activity_type = 'nutrition'

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

    @classmethod
    def expire_points(cls, tenant_id, user: User, amount: int, reason: str = "Points expired") -> RewardWallet:
        """
        Deducts expired points from a member's wallet balance and records an EXPIRE ledger entry.
        """
        with bypass_tenant_isolation():
            with transaction.atomic():
                wallet, _ = RewardWallet.objects.select_for_update().get_or_create(
                    tenant_id=tenant_id,
                    user=user,
                    defaults={'balance': 0, 'lifetime_earned': 0, 'lifetime_redeemed': 0}
                )

                deduct_amount = min(wallet.balance, max(0, amount))
                wallet.balance -= deduct_amount
                wallet.save()

                RewardPointLedger.objects.create(
                    tenant_id=tenant_id,
                    wallet=wallet,
                    user=user,
                    amount=-deduct_amount,
                    balance_after=wallet.balance,
                    transaction_type=TransactionType.EXPIRE,
                    description=reason,
                    is_expired=True
                )

                return wallet


class RewardRedemptionService:
    """
    Handles store redemptions, vouchers, and staff fulfillment.
    Guarantees strict concurrency locking and non-negative inventory.
    """

    @classmethod
    def redeem_item(cls, tenant_id, user: User, catalog_item_id, notes: str = "") -> RewardRedemption:
        """
        Executes point redemption flow:
        Client -> Select Reward -> Lock Item & Wallet -> Validate Points & Inventory ->
        Deduct Points -> Decrement Inventory -> Create Ledger Entry -> Create Redemption ->
        Generate Single-Use Code -> Associate Package (if configured).
        """
        with bypass_tenant_isolation():
            with transaction.atomic():
                # 1. Lock catalog item
                item = RewardCatalogItem.objects.select_for_update().filter(
                    tenant_id=tenant_id,
                    id=catalog_item_id,
                    is_active=True
                ).first()

                if not item:
                    raise ValueError("Reward catalog item not found or inactive.")

                # 2. Lock wallet
                wallet, _ = RewardWallet.objects.select_for_update().get_or_create(
                    tenant_id=tenant_id,
                    user=user,
                    defaults={'balance': 0, 'lifetime_earned': 0, 'lifetime_redeemed': 0}
                )

                # 3. Validate points sufficiency
                if wallet.balance < item.points_cost:
                    raise ValueError(f"Insufficient points. Required: {item.points_cost}, Available: {wallet.balance}")

                # 4. Validate inventory availability
                if item.stock_quantity is not None:
                    if item.stock_quantity <= 0:
                        raise ValueError("Reward item is out of stock.")
                    # Decrement inventory safely
                    item.stock_quantity -= 1
                    item.save(update_fields=['stock_quantity'])

                # 5. Deduct points from wallet
                wallet.balance -= item.points_cost
                wallet.lifetime_redeemed += item.points_cost
                wallet.save()

                # 6. Generate cryptographically secure, non-predictable, unique single-use code
                while True:
                    voucher_code = f"RDM-{secrets.token_hex(4).upper()}"
                    if not RewardRedemption.objects.filter(redemption_code=voucher_code).exists():
                        break

                # 7. Create Redemption
                redemption = RewardRedemption.objects.create(
                    tenant_id=tenant_id,
                    user=user,
                    catalog_item=item,
                    points_spent=item.points_cost,
                    status=RedemptionStatus.PENDING,
                    redemption_code=voucher_code,
                    notes=notes
                )

                # 8. Record Ledger Entry
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

                if item.package_type_id and getattr(item, 'allows_package_type', True):
                    from apps.scheduling.models import Package, PackageGrantSource
                    package = Package.objects.create(
                        tenant_id=tenant_id,
                        client=user,
                        package_type=item.package_type,
                        credits_remaining=item.package_type.credit_count,
                        expires_at=timezone.now() + timezone.timedelta(days=item.package_type.validity_days),
                        status='active',
                        grant_source=PackageGrantSource.REWARD_REDEMPTION,
                        is_complimentary=False,
                        price=0.00
                    )
                    redemption.granted_package = package
                    redemption.save(update_fields=['granted_package'])

                return redemption

    @classmethod
    def verify_code(cls, tenant_id, code: str) -> Dict[str, Any]:
        """
        Front-desk staff code verification.
        Validates code existence, tenant ownership, redemption status, and member details.
        """
        cleaned_code = str(code).strip().upper()
        with bypass_tenant_isolation():
            redemption = RewardRedemption.objects.filter(
                tenant_id=tenant_id,
                redemption_code__iexact=cleaned_code
            ).select_related('user', 'catalog_item', 'fulfilled_by').first()

            if not redemption:
                return {
                    'valid': False,
                    'error': 'Redemption code not found or belongs to another tenant.',
                    'code': cleaned_code
                }

            is_valid_to_fulfill = (redemption.status == RedemptionStatus.PENDING) or (redemption.status == RedemptionStatus.APPROVED)

            user_name = redemption.user.get_full_name() if redemption.user else ""
            if not user_name and redemption.user:
                user_name = redemption.user.email

            return {
                'valid': is_valid_to_fulfill,
                'status': redemption.status,
                'redemption_id': str(redemption.id),
                'code': redemption.redemption_code,
                'user_id': str(redemption.user_id),
                'user_email': redemption.user.email if redemption.user else "",
                'user_name': user_name,
                'catalog_item_id': str(redemption.catalog_item_id),
                'catalog_item_name': redemption.catalog_item.name,
                'item_type': redemption.catalog_item.item_type,
                'points_spent': redemption.points_spent,
                'created_at': redemption.created_at.isoformat(),
                'fulfilled_at': redemption.fulfilled_at.isoformat() if redemption.fulfilled_at else None,
                'fulfilled_by_email': redemption.fulfilled_by.email if redemption.fulfilled_by else None,
                'message': 'Code verified and ready for fulfillment.' if is_valid_to_fulfill else f'Redemption is already {redemption.status.lower()}.'
            }

    @classmethod
    def approve_redemption(cls, tenant_id, redemption_id, staff_user: User, notes: str = "") -> RewardRedemption:
        """
        Staff approves a pending redemption before fulfillment.
        """
        with bypass_tenant_isolation():
            with transaction.atomic():
                redemption = RewardRedemption.objects.select_for_update().filter(
                    tenant_id=tenant_id,
                    id=redemption_id
                ).first()

                if not redemption:
                    raise ValueError("Redemption record not found.")

                if redemption.status == RedemptionStatus.FULFILLED:
                    raise ValueError("Cannot approve an already fulfilled redemption.")
                if redemption.status == RedemptionStatus.CANCELLED:
                    raise ValueError("Cannot approve a cancelled redemption.")

                redemption.status = RedemptionStatus.APPROVED
                if notes:
                    redemption.notes = f"{redemption.notes}\nApproved by {staff_user.email}: {notes}".strip()
                redemption.save()

                return redemption

    @classmethod
    def fulfill_redemption(cls, tenant_id, redemption_id, staff_user: User, notes: str = "") -> RewardRedemption:
        """
        Staff marks a redemption as fulfilled and records staff ID & timestamp.
        """
        with bypass_tenant_isolation():
            with transaction.atomic():
                redemption = RewardRedemption.objects.select_for_update().filter(
                    tenant_id=tenant_id,
                    id=redemption_id
                ).first()

                if not redemption:
                    raise ValueError("Redemption record not found.")

                if redemption.status == RedemptionStatus.CANCELLED:
                    raise ValueError("Cannot fulfill a cancelled redemption.")

                if redemption.status == RedemptionStatus.FULFILLED:
                    return redemption

                redemption.status = RedemptionStatus.FULFILLED
                redemption.fulfilled_by = staff_user
                redemption.fulfilled_at = timezone.now()
                if notes:
                    redemption.notes = f"{redemption.notes}\nFulfilled by {staff_user.email}: {notes}".strip()
                redemption.save()

                return redemption

    @classmethod
    def fulfill_by_code(cls, tenant_id, code: str, staff_user: User, notes: str = "") -> RewardRedemption:
        """
        Staff fulfills a redemption directly by providing the voucher code.
        """
        cleaned_code = str(code).strip().upper()
        with bypass_tenant_isolation():
            with transaction.atomic():
                redemption = RewardRedemption.objects.select_for_update().filter(
                    tenant_id=tenant_id,
                    redemption_code__iexact=cleaned_code
                ).first()

                if not redemption:
                    raise ValueError("Redemption code not found or belongs to another tenant.")

                if redemption.status == RedemptionStatus.CANCELLED:
                    raise ValueError("Cannot fulfill a cancelled redemption.")

                if redemption.status == RedemptionStatus.FULFILLED:
                    raise ValueError("This redemption code has already been fulfilled.")

                redemption.status = RedemptionStatus.FULFILLED
                redemption.fulfilled_by = staff_user
                redemption.fulfilled_at = timezone.now()
                if notes:
                    redemption.notes = f"{redemption.notes}\nFulfilled by {staff_user.email}: {notes}".strip()
                redemption.save()

                return redemption

    @classmethod
    def cancel_and_refund_redemption(cls, tenant_id, redemption_id, staff_user: Optional[User] = None, reason: str = "") -> RewardRedemption:
        """
        Cancels a redemption, refunds points to user wallet, creates ledger reversal entry,
        restores catalog stock, and cancels any granted package credits.
        """
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
                
                if redemption.status == RedemptionStatus.FULFILLED:
                    raise ValueError("Cannot cancel an already fulfilled redemption.")

                redemption.status = RedemptionStatus.CANCELLED
                redemption.notes = f"{redemption.notes}\nCancelled by {staff_user.email if staff_user else 'Member'}: {reason}".strip()
                redemption.save()

                # 1. Refund points to wallet
                if redemption.points_spent > 0:
                    wallet = RewardWallet.objects.select_for_update().get(
                        tenant_id=tenant_id,
                        user=redemption.user
                    )
                    wallet.balance += redemption.points_spent
                    wallet.lifetime_redeemed -= redemption.points_spent
                    wallet.save()

                    # 2. Record ledger reversal entry
                    RewardPointLedger.objects.create(
                        tenant_id=tenant_id,
                        wallet=wallet,
                        user=redemption.user,
                        amount=redemption.points_spent,
                        balance_after=wallet.balance,
                        transaction_type=TransactionType.REVERSAL,
                        description=f"Refund for cancelled redemption {redemption.redemption_code}: {reason or 'Cancelled'}",
                        redemption=redemption
                    )

                # 3. Restock inventory item if applicable
                if redemption.catalog_item.stock_quantity is not None:
                    redemption.catalog_item.stock_quantity += 1
                    redemption.catalog_item.save(update_fields=['stock_quantity'])

                # 4. If a package was granted, cancel it
                if redemption.granted_package:
                    redemption.granted_package.status = 'canceled'
                    redemption.granted_package.save(update_fields=['status'])

                return redemption


class ClientReferralService:
    """
    Service for managing client-to-client referrals, validation, and reward fulfillment.
    Supports Option 1 (In-App Referral Code) and Option 2 (Universal Link / Deep-Link).
    """

    @classmethod
    def resolve_referrer_by_code(cls, tenant, code: str) -> Optional[User]:
        """
        Resolves a referrer User from a referral code within the specified tenant.
        """
        if not code or not str(code).strip():
            return None

        clean_code = str(code).strip().upper()

        with bypass_tenant_isolation():
            # 1. Check UserProfile.referral_code
            from apps.users.models import UserProfile
            profile = UserProfile.objects.filter(
                referral_code__iexact=clean_code,
                user__tenant=tenant,
                user__is_active=True
            ).select_related('user').first()

            if profile and profile.user:
                return profile.user

            # 2. Fallback check: deterministic code from user UUID (REF-<hex[:8]>)
            if clean_code.startswith("REF-"):
                candidate_prefix = clean_code[4:12].lower()
                for u in User.objects.filter(tenant=tenant, is_active=True):
                    if u.id.hex[:8].lower() == candidate_prefix:
                        try:
                            if hasattr(u, 'profile') and u.profile:
                                u.profile.referral_code = clean_code
                                u.profile.save(update_fields=['referral_code'])
                        except Exception:
                            pass
                        return u

        return None

    @classmethod
    def validate_referral(cls, tenant, referrer: User, referee: User) -> None:
        """
        Strictly validates the referral:
        - Referrer and referee cannot be the same user (anti-self-referral)
        - Both must belong to the tenant
        - Both must be active clients
        - Single-use enforcement: referee cannot have already been referred
        - Circular referral prevention
        """
        if referrer.id == referee.id:
            raise ValidationError("Self-referrals are not permitted.")

        if referrer.tenant_id != tenant.id:
            raise ValidationError("Referrer does not belong to this gym.")

        if referee.tenant_id != tenant.id:
            raise ValidationError("Referee does not belong to this gym.")

        if referrer.role != UserRole.CLIENT:
            raise ValidationError("Referrer must be an active gym client.")

        if referee.role != UserRole.CLIENT:
            raise ValidationError("Referee must be a gym client.")

        # Single-use check: has referee already been referred?
        from apps.rewards.models import ClientReferral, ProcessedRewardEvent
        with bypass_tenant_isolation():
            already_referred = ClientReferral.objects.filter(
                tenant=tenant,
                referee=referee,
                status=ClientReferral.ReferralStatus.COMPLETED
            ).exists()
            if already_referred:
                raise ValidationError("You have already claimed or received a referral bonus.")

            already_processed = ProcessedRewardEvent.all_objects.filter(
                tenant=tenant,
                event_type='referral.completed',
                payload__referee_id=str(referee.id)
            ).exists()
            if already_processed:
                raise ValidationError("A referral for this account has already been processed.")

            # Anti-circular check: did referee previously refer referrer?
            circular = ClientReferral.objects.filter(
                tenant=tenant,
                referrer=referee,
                referee=referrer,
                status=ClientReferral.ReferralStatus.COMPLETED
            ).exists()
            if circular:
                raise ValidationError("Circular referrals between the same accounts are not permitted.")

    @classmethod
    def process_referral(
        cls,
        tenant,
        referee: User,
        referral_code: str,
        method: str = 'CODE'
    ) -> Dict[str, Any]:
        """
        Executes referral completion and awards applicable rewards to both users.
        """
        referrer = cls.resolve_referrer_by_code(tenant, referral_code)
        if not referrer:
            raise ValidationError("Invalid or unresolvable referral code.")

        cls.validate_referral(tenant, referrer, referee)

        from apps.rewards.models import ClientReferral
        from apps.rewards.events import RewardEvent

        with transaction.atomic():
            # 1. Award Referrer (Client A)
            referrer_event = RewardEvent.create_referral_completed(
                tenant_id=tenant.id,
                referrer_id=referrer.id,
                referee_id=referee.id
            )
            referrer_txs = RewardEngineService.handle_event(referrer_event)
            points_awarded_referrer = sum(
                tx.action_payload.get('amount', 0)
                for tx in referrer_txs
                if tx.action_type == 'POINTS' and tx.result_status == ExecutionStatus.SUCCESS
            )

            # 2. Award Referee (Client B)
            referee_event = RewardEvent.create_referral_referee_reward(
                tenant_id=tenant.id,
                referee_id=referee.id,
                referrer_id=referrer.id
            )
            referee_txs = RewardEngineService.handle_event(referee_event)
            points_awarded_referee = sum(
                tx.action_payload.get('amount', 0)
                for tx in referee_txs
                if tx.action_type == 'POINTS' and tx.result_status == ExecutionStatus.SUCCESS
            )

            # 3. Record completed ClientReferral
            with bypass_tenant_isolation():
                referral_record = ClientReferral.objects.create(
                    tenant=tenant,
                    referrer=referrer,
                    referee=referee,
                    referral_code=referral_code.strip().upper(),
                    method=method,
                    status=ClientReferral.ReferralStatus.COMPLETED,
                    points_awarded_referrer=points_awarded_referrer,
                    points_awarded_referee=points_awarded_referee
                )

        return {
            'referral_id': str(referral_record.id),
            'referrer_id': str(referrer.id),
            'referrer_email': referrer.email,
            'referee_id': str(referee.id),
            'referee_email': referee.email,
            'referral_code': referral_record.referral_code,
            'method': method,
            'points_awarded_referrer': points_awarded_referrer,
            'points_awarded_referee': points_awarded_referee,
            'transactions_created': len(referrer_txs) + len(referee_txs),
            'completed_at': referral_record.completed_at.isoformat() if referral_record.completed_at else None,
        }

