from rest_framework import serializers
from apps.core.tenants.models import (
    Tenant, Plan, Feature, PlanEntitlement, 
    TenantSubscription, TenantEntitlementOverride
)

class FeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = Feature
        fields = ['id', 'key', 'description', 'data_type', 'created_at']

class PlanEntitlementSerializer(serializers.ModelSerializer):
    feature_key = serializers.ReadOnlyField(source='feature.key')
    
    class Meta:
        model = PlanEntitlement
        fields = ['id', 'feature', 'feature_key', 'value']

class PlanSerializer(serializers.ModelSerializer):
    entitlements = PlanEntitlementSerializer(many=True, read_only=True)
    
    class Meta:
        model = Plan
        fields = ['id', 'name', 'price', 'billing_cycle', 'is_public', 'entitlements']

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
        fields = ['id', 'name', 'subdomain', 'branding', 'is_active', 'created_at', 'current_subscription']
        read_only_fields = ['id', 'created_at']

    def get_current_subscription(self, obj):
        # Efficiently fetch active subscription
        sub = obj.subscriptions.filter(status='active').first()
        if sub:
            return TenantSubscriptionSerializer(sub).data
        return None

class OnboardTenantSerializer(serializers.Serializer):
    """
    Composite serializer for onboarding a new gym + owner.
    """
    gym_name = serializers.CharField(max_length=255)
    subdomain = serializers.SlugField(max_length=100)
    owner_email = serializers.EmailField()
    owner_password = serializers.CharField(write_only=True, min_length=8)
    initial_plan_id = serializers.UUIDField(required=False)
    