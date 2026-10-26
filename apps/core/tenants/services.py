"""
Tenant Entitlement Service

Centralized service for checking tenant feature access and limits.
Uses Redis caching to optimize performance for frequent entitlement checks.
"""
from django.core.cache import cache
from django.utils import timezone
from apps.core.tenants.models import (
    Tenant, Feature, TenantEntitlementOverride, 
    TenantSubscription, PlanEntitlement
)

from django.core.exceptions import ValidationError
from apps.core.tenants.models import (
    Tenant, Plan, TenantSubscription, 
    TenantEntitlementOverride, Feature
)
from apps.users.services import UserService
from apps.users.models import UserRole
from django.db import transaction


class TenantEntitlementService:
    """
    Service for checking tenant feature entitlements.
    
    Resolution order:
    1. Check TenantEntitlementOverride (tenant-specific)
    2. Check PlanEntitlement (from active subscription)
    3. Return default (False/0)
    
    Uses Redis caching with automatic invalidation.
    """
    
    CACHE_TTL = 300  # 5 minutes
    CACHE_PREFIX = "tenant:entitlements"
    
    @classmethod
    def _get_cache_key(cls, tenant_id):
        """Generate cache key for tenant entitlements."""
        return f"{cls.CACHE_PREFIX}:{str(tenant_id)}"
    
    @classmethod
    def _get_cached_entitlements(cls, tenant):
        """
        Retrieve cached entitlements for a tenant.
        Returns None if cache miss.
        """
        cache_key = cls._get_cache_key(tenant.id)
        return cache.get(cache_key)
    
    @classmethod
    def _set_cached_entitlements(cls, tenant, entitlements):
        """Cache entitlements for a tenant."""
        cache_key = cls._get_cache_key(tenant.id)
        cache.set(cache_key, entitlements, cls.CACHE_TTL)
    
    @classmethod
    def _invalidate_cache(cls, tenant):
        """Invalidate cached entitlements for a tenant."""
        cache_key = cls._get_cache_key(tenant.id)
        cache.delete(cache_key)
    
    @classmethod
    def _build_entitlements(cls, tenant):
        """
        Build entitlements dictionary for a tenant.
        
        Args:
            tenant: Tenant instance
            
        Returns:
            dict: {feature_key: value}
        """
        entitlements = {}
        
        # Step 1: Get plan entitlements from active subscription
        active_subscription = TenantSubscription.objects.filter(
            tenant=tenant,
            status='active'
        ).select_related('plan').first()
        
        if active_subscription:
            plan_entitlements = PlanEntitlement.objects.filter(
                plan=active_subscription.plan
            ).select_related('feature')
            
            for entitlement in plan_entitlements:
                entitlements[entitlement.feature.key] = entitlement.value
        
        # Step 2: Apply tenant-specific overrides
        now = timezone.now()
        overrides = TenantEntitlementOverride.objects.filter(
            tenant=tenant
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).select_related('feature')
        
        for override in overrides:
            entitlements[override.feature.key] = override.value
        
        return entitlements
    
    @classmethod
    def get_entitlements(cls, tenant):
        """
        Get entitlements for a tenant (cached or fresh).
        
        Args:
            tenant: Tenant instance
            
        Returns:
            dict: {feature_key: value}
        """
        # Try cache first
        entitlements = cls._get_cached_entitlements(tenant)
        
        if entitlements is None:
            # Cache miss - build and cache
            entitlements = cls._build_entitlements(tenant)
            cls._set_cached_entitlements(tenant, entitlements)
        
        return entitlements
    
    @classmethod
    def has_feature(cls, tenant, feature_key):
        """
        Check if a tenant has access to a boolean feature.
        
        Args:
            tenant: Tenant instance or tenant_id
            feature_key: Feature key string (e.g., 'api_access')
            
        Returns:
            bool: True if feature is enabled, False otherwise
            
        Example:
            >>> TenantEntitlementService.has_feature(tenant, 'custom_branding')
            True
        """
        if not isinstance(tenant, Tenant):
            tenant = Tenant.objects.get(id=tenant)
        
        entitlements = cls.get_entitlements(tenant)
        
        # Get value, default to False for boolean features
        value = entitlements.get(feature_key, False)
        
        # Handle different value formats
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value > 0
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes')
        
        return bool(value)
    
    @classmethod
    def get_limit(cls, tenant, feature_key):
        """
        Get numeric limit for a feature.
        
        Args:
            tenant: Tenant instance or tenant_id
            feature_key: Feature key string (e.g., 'max_members')
            
        Returns:
            int: Limit value
            
        Raises:
            ValueError: If feature not found or not numeric
            
        Example:
            >>> TenantEntitlementService.get_limit(tenant, 'max_members')
            1000
        """
        if not isinstance(tenant, Tenant):
            tenant = Tenant.objects.get(id=tenant)
        
        entitlements = cls.get_entitlements(tenant)
        
        if feature_key not in entitlements:
            raise ValueError(f"Feature '{feature_key}' not found for tenant {tenant.name}")
        
        value = entitlements[feature_key]
        
        # Convert to int
        try:
            return int(value)
        except (ValueError, TypeError):
            raise ValueError(
                f"Feature '{feature_key}' is not numeric (value: {value})"
            )
    
    @classmethod
    def invalidate_tenant_cache(cls, tenant):
        """
        Public method to invalidate cache for a tenant.
        Call this after modifying subscriptions or overrides.
        
        Args:
            tenant: Tenant instance or tenant_id
        """
        if not isinstance(tenant, Tenant):
            tenant = Tenant.objects.get(id=tenant)
        
        cls._invalidate_cache(tenant)


class TenantAdministrationService:
    """
    Service for Platform Admins to manage Tenants, Plans, and Entitlements.
    """

    @classmethod
    def resolve_tenant(cls, val):
        """
        Resolves a Tenant instance from a Tenant object, UUID, UUID string,
        dict containing an id/name/subdomain, or gym name / subdomain string.
        """
        if not val:
            return None
        if isinstance(val, Tenant):
            return val
        if isinstance(val, dict):
            val = val.get('id') or val.get('name') or val.get('subdomain')
            if not val:
                return None
        val_str = str(val).strip()
        if not val_str:
            return None
        # 1. Try UUID
        import uuid
        try:
            uuid_obj = uuid.UUID(val_str)
            t = Tenant.objects.filter(id=uuid_obj).first()
            if t:
                return t
        except (ValueError, AttributeError):
            pass
        # 2. Try subdomain
        t = Tenant.objects.filter(subdomain__iexact=val_str).first()
        if t:
            return t
        # 3. Try name
        t = Tenant.objects.filter(name__iexact=val_str).first()
        if t:
            return t
        return None

    @classmethod
    def ensure_referral_reward_record(cls, referrer, referred_tenant):
        """
        Ensures a ReferralReward record exists between referrer and referred_tenant.
        If the referred tenant has completed an active subscription, updates status and reward amount.
        """
        if not referrer or not referred_tenant or referrer.id == referred_tenant.id:
            return None

        from apps.core.tenants.models import ReferralReward
        from apps.payments.models import TenantBillingSubscription
        import decimal

        reward, _ = ReferralReward.objects.get_or_create(
            referrer=referrer,
            referred_tenant=referred_tenant,
            defaults={
                'reward_amount': decimal.Decimal('0.00'),
                'status': 'pending'
            }
        )

        # Check if referred tenant has an active paid feature billing subscription
        active_billing_sub = TenantBillingSubscription.all_objects.filter(
            tenant=referred_tenant,
            status=TenantBillingSubscription.StatusChoices.ACTIVE
        ).exclude(billing_plan__slug='free').select_related('billing_plan').order_by('-created_at').first()

        if active_billing_sub:
            return cls.process_tenant_referral_reward(
                tenant=referred_tenant,
                billing_subscription=active_billing_sub,
                is_trial=False
            )

        # Check if referred tenant has an active legacy core subscription
        active_core_sub = referred_tenant.subscriptions.filter(
            status='active'
        ).select_related('plan').order_by('-started_at').first()

        if active_core_sub:
            is_trial = bool(active_core_sub.trial_ends_at and active_core_sub.trial_ends_at > timezone.now())
            return cls.process_tenant_referral_reward(
                tenant=referred_tenant,
                core_subscription=active_core_sub,
                is_trial=is_trial
            )

        return reward

    @classmethod
    def sync_missing_referral_rewards(cls, referrer=None):
        """
        Self-healing audit: guarantees all tenants with a referring gym have a ReferralReward record.
        """
        qs = Tenant.objects.filter(referred_by__isnull=False)
        if referrer:
            qs = qs.filter(referred_by=referrer)

        for tenant in qs.select_related('referred_by'):
            cls.ensure_referral_reward_record(referrer=tenant.referred_by, referred_tenant=tenant)

    @classmethod
    @transaction.atomic
    def process_tenant_referral_reward(cls, tenant, billing_subscription=None, core_subscription=None, amount=None, is_trial=False):
        """
        Calculates and allocates referral reward (10% commission) for tenant's referrer upon subscription.
        """
        referrer = tenant.referred_by
        if not referrer:
            from apps.core.tenants.models import ReferralReward
            existing_reward = ReferralReward.objects.filter(referred_tenant=tenant).first()
            if existing_reward:
                referrer = existing_reward.referrer
            else:
                return None

        import decimal
        from django.db.models import Sum
        from apps.core.tenants.models import ReferralReward

        reward_percentage = decimal.Decimal('10.0')
        reward_amount = decimal.Decimal('0.00')

        if amount is not None and decimal.Decimal(str(amount)) > 0:
            paid_amount = decimal.Decimal(str(amount))
            reward_amount = (paid_amount * reward_percentage) / decimal.Decimal('100.0')
        elif billing_subscription:
            from apps.payments.models import PlatformLedger
            latest_ledger = PlatformLedger.all_objects.filter(
                tenant=tenant,
                status=PlatformLedger.StatusChoices.PAID,
                type=PlatformLedger.TransactionType.SUBSCRIPTION
            ).order_by('-created_at').first()
            if latest_ledger and latest_ledger.amount_gross > 0:
                paid_amount = decimal.Decimal(str(latest_ledger.amount_gross))
                reward_amount = (paid_amount * reward_percentage) / decimal.Decimal('100.0')
            else:
                features_total = billing_subscription.active_features.aggregate(total=Sum('price'))['total']
                if features_total and features_total > 0:
                    paid_amount = decimal.Decimal(str(features_total))
                    reward_amount = (paid_amount * reward_percentage) / decimal.Decimal('100.0')
                elif getattr(billing_subscription.billing_plan, 'price', None) is not None:
                    paid_amount = decimal.Decimal(str(billing_subscription.billing_plan.price))
                    reward_amount = (paid_amount * reward_percentage) / decimal.Decimal('100.0')
        elif core_subscription and core_subscription.plan and getattr(core_subscription.plan, 'price', None) is not None:
            plan_price = decimal.Decimal(str(core_subscription.plan.price))
            reward_amount = (plan_price * reward_percentage) / decimal.Decimal('100.0')

        reward_amount = reward_amount.quantize(decimal.Decimal('0.01'))

        reward, _ = ReferralReward.objects.get_or_create(
            referrer=referrer,
            referred_tenant=tenant,
            defaults={
                'reward_amount': reward_amount,
                'status': 'pending' if is_trial else ('paid' if reward_amount > 0 else 'pending')
            }
        )

        if billing_subscription:
            reward.billing_subscription = billing_subscription
        if core_subscription:
            reward.subscription = core_subscription

        if reward_amount > 0:
            reward.reward_amount = reward_amount

        reward.status = 'pending' if is_trial else ('paid' if reward.reward_amount > 0 else 'pending')
        reward.save()
        return reward

    @classmethod
    @transaction.atomic
    def onboard_tenant(cls, gym_name, subdomain, owner_email, owner_password, initial_plan_id=None, logo=None, branding=None, referred_by_id=None, affiliate_code=None):
        """
        Creates Tenant (with branding), Plan, and Owner.
        Supports linking to referring tenant or external affiliate partner.
        """
        if Tenant.objects.filter(subdomain=subdomain).exists():
            raise ValidationError(f"Subdomain '{subdomain}' is already taken.")

        # Fix: Default to empty dict if None to prevent null errors if DB enforces it
        branding_data = branding if branding else {}

        referred_by = None
        if referred_by_id:
            referred_by = cls.resolve_tenant(referred_by_id)
            if not referred_by:
                raise ValidationError("Referring Tenant does not exist.")

        affiliate = None
        if affiliate_code:
            from apps.core.tenants.models import AffiliatePartner
            if isinstance(affiliate_code, AffiliatePartner):
                if not affiliate_code.is_active:
                    raise ValidationError(f"The affiliate code '{affiliate_code.code}' is inactive.")
                affiliate = affiliate_code
            else:
                clean_code = str(affiliate_code).strip()
                if clean_code:
                    partner = AffiliatePartner.objects.filter(code__iexact=clean_code).first()
                    if not partner:
                        raise ValidationError(f"The affiliate code '{clean_code}' is invalid.")
                    if not partner.is_active:
                        raise ValidationError(f"The affiliate code '{clean_code}' is inactive.")
                    affiliate = partner

        tenant = Tenant.objects.create(
            name=gym_name, 
            subdomain=subdomain,
            logo=logo,
            branding=branding_data,
            referred_by=referred_by,
            affiliate=affiliate
        )

        # Immediately record the referral relationship if referred by another gym
        if referred_by:
            from apps.core.tenants.models import ReferralReward
            ReferralReward.objects.get_or_create(
                referrer=referred_by,
                referred_tenant=tenant,
                defaults={
                    'reward_amount': 0.00,
                    'status': 'pending'
                }
            )

        if initial_plan_id:
            try:
                plan = Plan.objects.get(id=initial_plan_id)
                cls.assign_plan(tenant, plan)
            except Plan.DoesNotExist:
                raise ValidationError("Invalid Plan ID provided.")

        UserService.create_user_with_profile(
            email=owner_email,
            password=owner_password,
            role=UserRole.GYM_OWNER,
            tenant=tenant,
            nickname="Admin"
        )

        return tenant

    @classmethod
    @transaction.atomic
    def assign_plan(cls, tenant, plan, trial_days=None):
        """
        Switches a tenant to a new plan.
        Cancels old active subscriptions and creates a new one.
        If trial_days is provided, sets trial_ends_at.
        """
        from datetime import timedelta
        now = timezone.now()

        # Cancel current active subscriptions
        TenantSubscription.objects.filter(
            tenant=tenant, 
            status='active'
        ).update(
            status='canceled', 
            ends_at=now
        )

        trial_ends_at = now + timedelta(days=trial_days) if trial_days else None

        # Create new subscription
        subscription = TenantSubscription.objects.create(
            tenant=tenant,
            plan=plan,
            status='active',
            started_at=now,
            trial_ends_at=trial_ends_at
        )

        # Calculate and record referral reward if referred by another tenant
        if tenant.referred_by:
            cls.process_tenant_referral_reward(
                tenant=tenant,
                core_subscription=subscription,
                is_trial=bool(trial_ends_at)
            )

        if getattr(tenant, 'affiliate', None) and not trial_ends_at:
            from apps.core.tenants.affiliate_service import AffiliateService
            AffiliateService.award_initial_subscription_points(tenant, subscription)
         
        return subscription

    @staticmethod
    def set_feature_override(tenant, feature_id, value, expires_at=None):
        """
        Allows/Disallows a specific feature for a tenant explicitly.
        """
        feature = Feature.objects.get(id=feature_id)
        
        override, created = TenantEntitlementOverride.objects.update_or_create(
            tenant=tenant,
            feature=feature,
            defaults={
                'value': value,
                'expires_at': expires_at
            }
        )
        return override

# Import models for Q lookup
from django.db import models
