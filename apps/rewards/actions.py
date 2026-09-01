"""
Reward Action Handlers & Executor Registry

Executes reward side-effects (points, badges, packages, discounts, notifications)
atomically with complete audit trails.
"""
import secrets
from typing import Dict, Any, Tuple
from django.db import transaction
from django.utils import timezone
from apps.core.tenants.context import bypass_tenant_isolation
from apps.users.models import User
from apps.rewards.models import (
    RewardWallet, RewardPointLedger, Badge, UserBadge,
    RewardTier, RewardRedemption, RewardCatalogItem, TransactionType
)


class ActionExecutionResult:
    def __init__(self, success: bool, action_type: str, result_data: Dict[str, Any], error: str = ""):
        self.success = success
        self.action_type = action_type
        self.result_data = result_data
        self.error = error


class ActionHandlerRegistry:
    """
    Registry for reward actions.
    """

    @classmethod
    def execute_action(
        cls,
        action: Dict[str, Any],
        user: User,
        tenant_id,
        rule=None,
        transaction_record=None
    ) -> ActionExecutionResult:
        with bypass_tenant_isolation():
            action_type = action.get('type', '').upper()

            if action_type == 'POINTS':
                return cls._handle_points(action, user, tenant_id, transaction_record)

            elif action_type == 'BADGE':
                return cls._handle_badge(action, user, tenant_id, rule)

            elif action_type in ['FREE_CLASS', 'PACKAGE_CREDIT']:
                return cls._handle_package_credit(action, user, tenant_id)

            elif action_type == 'DISCOUNT_CODE':
                return cls._handle_discount_code(action, user, tenant_id)

            elif action_type == 'NOTIFICATION':
                return cls._handle_notification(action, user, tenant_id)

            elif action_type == 'TIER_UPGRADE':
                return cls._handle_tier_upgrade(action, user, tenant_id)

            return ActionExecutionResult(
                success=False,
                action_type=action_type,
                result_data={},
                error=f"Unsupported action type: {action_type}"
            )

    @classmethod
    def _handle_points(cls, action: Dict[str, Any], user: User, tenant_id, transaction_record) -> ActionExecutionResult:
        base_points = int(action.get('amount', 0))
        if base_points <= 0:
            return ActionExecutionResult(success=True, action_type='POINTS', result_data={'points_added': 0})

        # Lock the wallet row for concurrency safety
        wallet, _ = RewardWallet.objects.select_for_update().get_or_create(
            tenant_id=tenant_id,
            user=user,
            defaults={'balance': 0, 'lifetime_earned': 0, 'lifetime_redeemed': 0}
        )

        # Check for active tier multiplier
        multiplier = 1.0
        if wallet.current_tier and wallet.current_tier.multiplier:
            multiplier = float(wallet.current_tier.multiplier)

        points_awarded = int(base_points * multiplier)

        # Update Wallet balance & lifetime stats
        wallet.balance += points_awarded
        wallet.lifetime_earned += points_awarded

        # Check for Tier Progression
        next_tier = RewardTier.objects.filter(
            tenant_id=tenant_id,
            threshold_points__lte=wallet.lifetime_earned
        ).order_by('-threshold_points').first()

        tier_upgraded = False
        if next_tier and next_tier != wallet.current_tier:
            wallet.current_tier = next_tier
            tier_upgraded = True

        wallet.save()

        # Record Ledger Entry
        description = action.get('description') or f"Earned {points_awarded} points from reward rule"
        RewardPointLedger.objects.create(
            tenant_id=tenant_id,
            wallet=wallet,
            user=user,
            amount=points_awarded,
            balance_after=wallet.balance,
            transaction_type=TransactionType.EARN,
            description=description,
            source_transaction=transaction_record
        )

        return ActionExecutionResult(
            success=True,
            action_type='POINTS',
            result_data={
                'points_awarded': points_awarded,
                'base_points': base_points,
                'multiplier': multiplier,
                'new_balance': wallet.balance,
                'tier_upgraded': tier_upgraded,
                'current_tier': wallet.current_tier.name if wallet.current_tier else None
            }
        )

    @classmethod
    def _handle_badge(cls, action: Dict[str, Any], user: User, tenant_id, rule) -> ActionExecutionResult:
        badge_slug = action.get('badge_slug') or action.get('slug')
        badge_id = action.get('badge_id')

        badge = None
        if badge_id:
            badge = Badge.objects.filter(tenant_id=tenant_id, id=badge_id).first()
        elif badge_slug:
            badge = Badge.objects.filter(tenant_id=tenant_id, slug=badge_slug).first()

        if not badge:
            return ActionExecutionResult(
                success=False,
                action_type='BADGE',
                result_data={},
                error=f"Badge '{badge_slug or badge_id}' not found for tenant."
            )

        # Idempotently award badge
        user_badge, created = UserBadge.objects.get_or_create(
            tenant_id=tenant_id,
            user=user,
            badge=badge,
            defaults={'source_rule': rule}
        )

        return ActionExecutionResult(
            success=True,
            action_type='BADGE',
            result_data={
                'badge_id': str(badge.id),
                'badge_name': badge.name,
                'newly_awarded': created
            }
        )

    @classmethod
    def _handle_package_credit(cls, action: Dict[str, Any], user: User, tenant_id) -> ActionExecutionResult:
        from apps.scheduling.models import Package, PackageType

        package_type_id = action.get('package_type_id')
        credits = int(action.get('credits', 1))
        validity_days = int(action.get('validity_days', 30))

        package_type = None
        if package_type_id:
            package_type = PackageType.objects.filter(tenant_id=tenant_id, id=package_type_id).first()

        if not package_type:
            # Fallback to any active package type or create ad-hoc complimentary package
            package_type = PackageType.objects.filter(tenant_id=tenant_id, is_active=True).first()

        if not package_type:
            return ActionExecutionResult(
                success=False,
                action_type='PACKAGE_CREDIT',
                result_data={},
                error="No PackageType found to assign complimentary class credit."
            )

        expires_at = timezone.now() + timezone.timedelta(days=validity_days)
        new_package = Package.objects.create(
            tenant_id=tenant_id,
            client=user,
            package_type=package_type,
            credits_remaining=credits,
            expires_at=expires_at,
            status='active',
            is_complimentary=True,
            price=0.00
        )

        return ActionExecutionResult(
            success=True,
            action_type='PACKAGE_CREDIT',
            result_data={
                'package_id': str(new_package.id),
                'credits': credits,
                'package_name': package_type.name,
                'expires_at': str(expires_at)
            }
        )

    @classmethod
    def _handle_discount_code(cls, action: Dict[str, Any], user: User, tenant_id) -> ActionExecutionResult:
        discount_name = action.get('name', 'Reward Discount Voucher')
        discount_code = f"RW-{secrets.token_hex(4).upper()}"
        
        # Create a catalog item if needed or direct redemption
        catalog_item, _ = RewardCatalogItem.objects.get_or_create(
            tenant_id=tenant_id,
            name=discount_name,
            defaults={
                'item_type': 'DISCOUNT_CODE',
                'points_cost': 0,
                'description': f"Earned via reward rule: {discount_name}"
            }
        )

        redemption = RewardRedemption.objects.create(
            tenant_id=tenant_id,
            user=user,
            catalog_item=catalog_item,
            points_spent=0,
            status='APPROVED',
            redemption_code=discount_code,
            notes=action.get('notes', '')
        )

        return ActionExecutionResult(
            success=True,
            action_type='DISCOUNT_CODE',
            result_data={
                'redemption_id': str(redemption.id),
                'discount_code': discount_code,
                'discount_name': discount_name
            }
        )

    @classmethod
    def _handle_notification(cls, action: Dict[str, Any], user: User, tenant_id) -> ActionExecutionResult:
        title = action.get('title', 'Reward Unlocked! 🎉')
        body = action.get('body', 'You earned a new reward!')
        
        try:
            from apps.notifications.services import NotificationService
            from apps.notifications.events import NotificationEvent
            
            NotificationService.handle_event(NotificationEvent(
                tenant_id=tenant_id,
                recipient_id=user.id,
                context_data={
                    'title': title,
                    'body': body,
                    'client_name': getattr(getattr(user, 'profile', None), 'first_name', user.email),
                }
            ))
            return ActionExecutionResult(
                success=True,
                action_type='NOTIFICATION',
                result_data={'title': title, 'recipient': user.email}
            )
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                action_type='NOTIFICATION',
                result_data={},
                error=f"Notification error: {str(e)}"
            )

    @classmethod
    def _handle_tier_upgrade(cls, action: Dict[str, Any], user: User, tenant_id) -> ActionExecutionResult:
        tier_name = action.get('tier_name')
        tier = RewardTier.objects.filter(tenant_id=tenant_id, name__iexact=tier_name).first()
        if not tier:
            return ActionExecutionResult(success=False, action_type='TIER_UPGRADE', result_data={}, error=f"Tier '{tier_name}' not found.")

        wallet, _ = RewardWallet.objects.select_for_update().get_or_create(
            tenant_id=tenant_id,
            user=user,
            defaults={'balance': 0, 'lifetime_earned': 0}
        )
        wallet.current_tier = tier
        wallet.save()

        return ActionExecutionResult(
            success=True,
            action_type='TIER_UPGRADE',
            result_data={'tier_name': tier.name, 'multiplier': str(tier.multiplier)}
        )
