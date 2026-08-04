from rest_framework import serializers
from .models import PlatformLedger, TenantPayout, TenantSubscription, FeatureToggle

class PlatformLedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformLedger
        fields = [
            'id', 'tenant', 'transaction_id', 'amount_gross', 
            'platform_fee', 'amount_net', 'currency', 'type', 
            'description', 'status', 'payout', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

class TenantPayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantPayout
        fields = ['id', 'tenant', 'amount', 'currency', 'status', 'stripe_payout_id', 'created_at']
        read_only_fields = ['id', 'created_at']

class TenantSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantSubscription
        fields = ['id', 'tenant', 'plan_name', 'stripe_subscription_id', 'status', 'current_period_end', 'created_at']
        read_only_fields = ['id', 'created_at']

class FeatureToggleSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeatureToggle
        fields = ['id', 'tenant', 'feature_name', 'is_enabled', 'stripe_price_id', 'created_at']
        read_only_fields = ['id', 'created_at']
