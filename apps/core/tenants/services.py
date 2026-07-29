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

    @staticmethod
    @transaction.atomic
    def onboard_tenant(gym_name, subdomain, owner_email, owner_password, initial_plan_id=None, branding=None, referred_by_id=None):
        """
        Creates Tenant (with branding), Plan, and Owner.
        """
        if Tenant.objects.filter(subdomain=subdomain).exists():
            raise ValidationError(f"Subdomain '{subdomain}' is already taken.")

        # Fix: Default to empty dict if None to prevent null errors if DB enforces it
        branding_data = branding if branding else {}

        referred_by = None
        if referred_by_id:
            try:
                referred_by = Tenant.objects.get(id=referred_by_id)
            except Tenant.DoesNotExist:
                raise ValidationError("Referring Tenant does not exist.")

        tenant = Tenant.objects.create(
            name=gym_name, 
            subdomain=subdomain,
            branding=branding_data,
            referred_by=referred_by
        )

        if initial_plan_id:
            try:
                plan = Plan.objects.get(id=initial_plan_id)
                TenantAdministrationService.assign_plan(tenant, plan)
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

    @staticmethod
    @transaction.atomic
    def assign_plan(tenant, plan, trial_days=None):
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
            import decimal
            # Default commission: 10% of subscription plan price
            reward_percentage = decimal.Decimal(10.0)
            plan_price = decimal.Decimal(str(plan.price))
            reward_amount = (plan_price * reward_percentage) / decimal.Decimal(100.0)
            
            if reward_amount > 0:
                from apps.core.tenants.models import ReferralReward
                ReferralReward.objects.create(
                    referrer=tenant.referred_by,
                    referred_tenant=tenant,
                    subscription=subscription,
                    reward_amount=reward_amount,
                    status='pending' if trial_ends_at else 'paid'
                )
        
        # Signals will automatically invalidate cache (Priority 2 Impl)
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
