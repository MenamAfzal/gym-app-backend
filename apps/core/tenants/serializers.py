from rest_framework import serializers
from apps.core.tenants.models import (
    Tenant, Plan, Feature, PlanEntitlement, 
    TenantSubscription, TenantEntitlementOverride, ReferralReward,
    AffiliatePartner, AffiliatePointsTransaction
)
from django.db import transaction
from apps.core.tenants.services import TenantEntitlementService
from apps.payments.models import TenantBillingSubscription

class FeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feature
        fields = ['id', 'key', 'description', 'data_type', 'created_at']

class TenantLogoSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ['id', 'name', 'subdomain', 'logo']

class PlanEntitlementSerializer(serializers.ModelSerializer):
    feature_key = serializers.ReadOnlyField(source='feature.key')
    
    class Meta:
        model = PlanEntitlement
        fields = ['id', 'feature', 'feature_key', 'value']

class PlanSerializer(serializers.ModelSerializer):
    # Change from read_only=True to allow receiving feature data
    entitlements = PlanEntitlementSerializer(many=True) 

    class Meta:
        model = Plan
        fields = ['id', 'name', 'price', 'billing_cycle', 'is_public', 'entitlements']

    def create(self, validated_data):
        entitlements_data = validated_data.pop('entitlements')
        plan = Plan.objects.create(**validated_data)
        
        # Link Features to this Plan via PlanEntitlement model
        for item in entitlements_data:
            PlanEntitlement.objects.create(plan=plan, **item)
        return plan
    
    @transaction.atomic
    def update(self, instance, validated_data):
        # 1. Extract entitlements data
        entitlements_data = validated_data.pop('entitlements', None)
        
        # 2. Update basic Plan fields (name, price, etc.)
        instance = super().update(instance, validated_data)

        # 3. Handle Nested Entitlements Sync
        if entitlements_data is not None:
            #  Clear existing entitlements and recreate
            instance.entitlements.all().delete()
            for item in entitlements_data:
                PlanEntitlement.objects.create(plan=instance, **item)

            # 4. CACHE INVALIDATION (Crucial for "Fully Managed")

            affected_tenants = instance.subscriptions.filter(status='active').values_list('tenant_id', flat=True)
            for tenant_id in affected_tenants:
                TenantEntitlementService.invalidate_tenant_cache(tenant_id)
        
        return instance

class TenantSubscriptionSerializer(serializers.ModelSerializer):
    plan_name = serializers.ReadOnlyField(source='plan.name')
    
    class Meta:
        model = TenantSubscription
        fields = ['id', 'plan', 'plan_name', 'status', 'started_at', 'ends_at']

class TenantEntitlementOverrideSerializer(serializers.ModelSerializer):
    feature_key = serializers.ReadOnlyField(source='feature.key')

    class Meta:
        model = TenantEntitlementOverride
        fields = ['id', 'feature', 'feature_key', 'value', 'expires_at']

class TenantSerializer(serializers.ModelSerializer):
    current_subscription = serializers.SerializerMethodField()
    
    class Meta:
        model = Tenant
        fields = ['id', 'name', 'subdomain', 'logo', 'branding', 'is_active', 'created_at', 'current_subscription']
        read_only_fields = ['id', 'created_at']

    def get_current_subscription(self, obj):
        # Efficiently fetch active subscription
        sub = obj.subscriptions.filter(status='active').first()
        if sub:
            return TenantSubscriptionSerializer(sub).data
        return None

class OnboardTenantSerializer(serializers.Serializer):
    gym_name = serializers.CharField(max_length=255)
    subdomain = serializers.SlugField(max_length=100)
    owner_email = serializers.EmailField()
    owner_password = serializers.CharField(write_only=True, min_length=8)
    initial_plan_id = serializers.UUIDField(required=False)
    logo = serializers.ImageField(required=False)
    branding = serializers.JSONField(required=False, default=dict)
    referred_by_id = serializers.UUIDField(required=False, allow_null=True)
    affiliate_code = serializers.CharField(required=False, allow_null=True, allow_blank=True)

class TenantSerializer(serializers.ModelSerializer):
    current_subscription = serializers.SerializerMethodField()
    entitlements = serializers.SerializerMethodField() # New Requirement
    
    class Meta:
        model = Tenant
        fields = [
            'id', 'name', 'subdomain', 'logo', 'branding', 
            'is_active', 'created_at', 
            'current_subscription', 'entitlements'
        ]
        read_only_fields = ['id', 'created_at']

    def get_current_subscription(self, obj):

        
        if hasattr(obj, '_prefetched_billing_subs'):
            billing_sub = next(
                (s for s in obj._prefetched_billing_subs
                 if s.status == TenantBillingSubscription.StatusChoices.ACTIVE),
                None
            )
        else: 
            # Fallback for single-instance retrieval (detail endpoint)
            billing_sub = (
                TenantBillingSubscription.all_objects
                .filter(tenant=obj, status=TenantBillingSubscription.StatusChoices.ACTIVE)
                .select_related('billing_plan')
                .order_by('-created_at')
                .first()
            )

        if billing_sub:
            return {
                'plan_name': billing_sub.billing_plan.name,
                'plan_slug': billing_sub.billing_plan.slug,
                'status': billing_sub.status,
                'current_period_end': billing_sub.current_period_end,
            }
 
        if hasattr(obj, '_prefetched_objects_cache') and 'subscriptions' in obj._prefetched_objects_cache:
            active_sub = next(
                (s for s in obj.subscriptions.all() if s.status == 'active'),
                None
            )
            return TenantSubscriptionSerializer(active_sub).data if active_sub else None

        sub = obj.subscriptions.filter(status='active').first()
        return TenantSubscriptionSerializer(sub).data if sub else None


    def get_entitlements(self, obj):
        # Returns the resolved list of features this tenant has access to
        return TenantEntitlementService.get_entitlements(obj)

class CheckoutInitSerializer(serializers.Serializer):
    """
    Validates input for initiating a Stripe Checkout Session.
    """
    plan_id = serializers.UUIDField()
    success_url = serializers.URLField(required=False)
    cancel_url = serializers.URLField(required=False)

    def validate_plan_id(self, value):
        # Circular import avoidance
        from apps.core.tenants.models import Plan
        try:
            plan = Plan.objects.get(id=value)
            if not plan.stripe_price_id:
                raise serializers.ValidationError("This plan is not available for purchase (Missing Stripe Price ID).")
            return plan
        except Plan.DoesNotExist:
            raise serializers.ValidationError("Invalid Plan ID.")


class ReferralRewardSerializer(serializers.ModelSerializer):
    referrer_name = serializers.ReadOnlyField(source='referrer.name')
    referred_tenant_name = serializers.ReadOnlyField(source='referred_tenant.name')
    plan_name = serializers.ReadOnlyField(source='subscription.plan.name')

    class Meta:
        model = ReferralReward
        fields = [
            'id', 'referrer', 'referrer_name', 
            'referred_tenant', 'referred_tenant_name', 
            'subscription', 'plan_name', 
            'reward_amount', 'status', 'created_at'
        ]


class AffiliateRegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    code = serializers.CharField(max_length=50, required=False, allow_blank=True, allow_null=True)
    company_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    website_or_social = serializers.URLField(required=False, allow_blank=True, default="")


class AffiliatePartnerSerializer(serializers.ModelSerializer):
    email = serializers.ReadOnlyField(source='user.email')
    first_name = serializers.ReadOnlyField(source='user.first_name')
    last_name = serializers.ReadOnlyField(source='user.last_name')
    total_gyms_referred = serializers.SerializerMethodField()
    active_gyms_referred = serializers.SerializerMethodField()

    class Meta:
        model = AffiliatePartner
        fields = [
            'id', 'email', 'first_name', 'last_name',
            'code', 'company_name', 'website_or_social',
            'points_balance', 'lifetime_points_earned',
            'is_active', 'total_gyms_referred', 'active_gyms_referred',
            'created_at'
        ]
        read_only_fields = ['id', 'points_balance', 'lifetime_points_earned', 'created_at']

    def get_total_gyms_referred(self, obj):
        return obj.referred_tenants.count()

    def get_active_gyms_referred(self, obj):
        return obj.referred_tenants.filter(is_active=True).count()


class AffiliatePointsTransactionSerializer(serializers.ModelSerializer):
    gym_name = serializers.SerializerMethodField()

    class Meta:
        model = AffiliatePointsTransaction
        fields = [
            'id', 'points', 'transaction_type', 'description',
            'gym_name', 'tenant', 'subscription', 'metadata',
            'created_at'
        ]

    def get_gym_name(self, obj):
        if obj.tenant:
            return obj.tenant.name
        return obj.metadata.get('gym_name', 'Unknown Gym')


class AffiliateReferredGymSerializer(serializers.ModelSerializer):
    plan_name = serializers.SerializerMethodField()
    subscription_status = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = ['id', 'name', 'subdomain', 'logo', 'is_active', 'plan_name', 'subscription_status', 'created_at']

    def get_plan_name(self, obj):
        sub = obj.subscriptions.filter(status='active').select_related('plan').first()
        return sub.plan.name if sub and sub.plan else None

    def get_subscription_status(self, obj):
        sub = obj.subscriptions.filter(status='active').first()
        return sub.status if sub else 'none'


class AffiliatePointsAdjustmentSerializer(serializers.Serializer):
    points = serializers.IntegerField(help_text="Points to add (positive) or deduct (negative)")
    reason = serializers.CharField(max_length=255, required=True)
        