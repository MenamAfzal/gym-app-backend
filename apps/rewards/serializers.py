"""
Reward Engine Serializers

Comprehensive, optimized serializers for Admin rule configuration and Client wallet/catalog UX.
Prevents N+1 queries through queryset-level annotations and select_related bindings.
"""
from rest_framework import serializers
from apps.rewards.models import (
    RewardProgram, RewardRule, RewardRuleVersion, Badge, RewardTier,
    RewardWallet, RewardPointLedger, UserBadge, UserStreak,
    RewardCatalogItem, RewardRedemption, RewardTransaction
)


class RewardProgramSerializer(serializers.ModelSerializer):
    rules_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = RewardProgram
        fields = [
            'id', 'name', 'program_type', 'description', 'status',
            'start_date', 'end_date', 'metadata', 'rules_count',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'rules_count']


class RewardRuleVersionSerializer(serializers.ModelSerializer):
    created_by_email = serializers.ReadOnlyField(source='created_by.email')

    class Meta:
        model = RewardRuleVersion
        fields = [
            'id', 'version', 'trigger_config_snapshot', 'conditions_snapshot',
            'actions_snapshot', 'change_summary', 'created_by_email', 'created_at'
        ]


class RewardRuleSerializer(serializers.ModelSerializer):
    program = serializers.PrimaryKeyRelatedField(queryset=RewardProgram.all_objects.all())
    program_name = serializers.ReadOnlyField(source='program.name')
    versions = RewardRuleVersionSerializer(many=True, read_only=True)

    class Meta:
        model = RewardRule
        fields = [
            'id', 'program', 'program_name', 'name', 'description',
            'event_type', 'status', 'version', 'trigger_config',
            'conditions', 'actions', 'max_executions_per_user',
            'max_executions_per_period', 'period_window_days',
            'priority', 'versions', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'version', 'created_at', 'updated_at', 'versions']

    def update(self, instance, validated_data):
        # Auto-increment version if configuration changes
        config_fields = ['trigger_config', 'conditions', 'actions', 'event_type']
        has_config_change = any(f in validated_data for f in config_fields)
        
        if has_config_change:
            instance.version += 1

        return super().update(instance, validated_data)


class BadgeSerializer(serializers.ModelSerializer):
    awarded_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Badge
        fields = [
            'id', 'name', 'slug', 'description', 'icon_url',
            'category', 'is_active', 'awarded_count', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'awarded_count']


class RewardTierSerializer(serializers.ModelSerializer):
    program = serializers.PrimaryKeyRelatedField(queryset=RewardProgram.all_objects.all())
    badge = serializers.PrimaryKeyRelatedField(queryset=Badge.all_objects.all(), required=False, allow_null=True)
    badge_details = BadgeSerializer(source='badge', read_only=True)

    class Meta:
        model = RewardTier
        fields = [
            'id', 'program', 'name', 'threshold_points', 'multiplier',
            'perks_description', 'badge', 'badge_details', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']


class RewardCatalogItemSerializer(serializers.ModelSerializer):
    package_type_name = serializers.ReadOnlyField(source='package_type.name')

    class Meta:
        model = RewardCatalogItem
        fields = [
            'id', 'name', 'description', 'points_cost', 'item_type',
            'stock_quantity', 'is_active', 'image_url', 'package_type',
            'package_type_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class RewardPointLedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = RewardPointLedger
        fields = [
            'id', 'amount', 'balance_after', 'transaction_type',
            'description', 'created_at'
        ]
        read_only_fields = fields


class UserBadgeSerializer(serializers.ModelSerializer):
    badge = BadgeSerializer(read_only=True)

    class Meta:
        model = UserBadge
        fields = ['id', 'badge', 'earned_at']
        read_only_fields = fields


class UserStreakSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserStreak
        fields = ['id', 'activity_type', 'current_streak', 'longest_streak', 'last_activity_date']
        read_only_fields = fields


class RewardWalletSerializer(serializers.ModelSerializer):
    current_tier_name = serializers.ReadOnlyField(source='current_tier.name')
    current_tier_multiplier = serializers.ReadOnlyField(source='current_tier.multiplier')
    user_email = serializers.ReadOnlyField(source='user.email')

    class Meta:
        model = RewardWallet
        fields = [
            'id', 'user_email', 'balance', 'lifetime_earned',
            'lifetime_redeemed', 'current_tier', 'current_tier_name',
            'current_tier_multiplier', 'updated_at'
        ]
        read_only_fields = [f for f in fields if f != 'balance']


class RewardRedemptionSerializer(serializers.ModelSerializer):
    catalog_item_name = serializers.ReadOnlyField(source='catalog_item.name')
    user_email = serializers.ReadOnlyField(source='user.email')
    fulfilled_by_email = serializers.ReadOnlyField(source='fulfilled_by.email')

    class Meta:
        model = RewardRedemption
        fields = [
            'id', 'user', 'user_email', 'catalog_item', 'catalog_item_name',
            'points_spent', 'status', 'redemption_code', 'fulfilled_by_email',
            'fulfilled_at', 'notes', 'created_at'
        ]
        read_only_fields = [
            'id', 'user', 'user_email', 'catalog_item_name', 'points_spent',
            'redemption_code', 'fulfilled_by_email', 'fulfilled_at', 'created_at'
        ]


class RedemptionCreateSerializer(serializers.Serializer):
    catalog_item_id = serializers.UUIDField(required=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class PointsAdjustmentSerializer(serializers.Serializer):
    user_id = serializers.UUIDField(required=True)
    amount = serializers.IntegerField(required=True)
    reason = serializers.CharField(required=True, max_length=255)


class RewardTransactionSerializer(serializers.ModelSerializer):
    rule_name = serializers.ReadOnlyField(source='rule.name')
    user_email = serializers.ReadOnlyField(source='user.email')

    class Meta:
        model = RewardTransaction
        fields = [
            'id', 'user_email', 'rule_name', 'rule_version', 'action_type',
            'action_payload', 'result_status', 'result_data', 'milestone_key',
            'created_at'
        ]
        read_only_fields = fields
