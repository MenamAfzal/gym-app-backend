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


from urllib.parse import urlparse


def _extract_server_path(url_str):
    if not url_str or not isinstance(url_str, str):
        return url_str
    parsed = urlparse(url_str)
    if parsed.path:
        return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path
    return url_str


class BadgeSerializer(serializers.ModelSerializer):
    awarded_count = serializers.IntegerField(read_only=True, default=0)
    image = serializers.ImageField(required=False, allow_null=True)
    file = serializers.ImageField(required=False, write_only=True, allow_null=True)

    class Meta:
        model = Badge
        fields = [
            'id', 'name', 'slug', 'description', 'image', 'file', 'icon_url',
            'category', 'is_active', 'awarded_count', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'awarded_count']

    def to_internal_value(self, data):
        # Support both 'image' and 'file' field names in multipart uploads
        if hasattr(data, 'copy') and hasattr(data, 'get'):
            if data.get('file') and not data.get('image'):
                data = data.copy()
                data['image'] = data.get('file')
        elif isinstance(data, dict):
            if data.get('file') and not data.get('image'):
                data = dict(data)
                data['image'] = data.get('file')
        ret = super().to_internal_value(data)
        if 'file' in ret and not ret.get('image'):
            ret['image'] = ret.pop('file')
        elif 'file' in ret:
            ret.pop('file')
        return ret

    def to_representation(self, instance):
        ret = super().to_representation(instance)

        # Return only the server path without base URL for image and icon_url
        if instance.image:
            try:
                ret['image'] = instance.image.url
            except Exception:
                if ret.get('image'):
                    ret['image'] = _extract_server_path(ret['image'])
        elif ret.get('image'):
            ret['image'] = _extract_server_path(ret['image'])

        if instance.image:
            try:
                ret['icon_url'] = instance.image.url
            except Exception:
                if ret.get('icon_url'):
                    ret['icon_url'] = _extract_server_path(ret['icon_url'])
        elif ret.get('icon_url'):
            ret['icon_url'] = _extract_server_path(ret['icon_url'])

        return ret


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
            'stock_quantity', 'is_active', 'image', 'image_url', 'package_type',
            'package_type_name', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def to_internal_value(self, data):
        if hasattr(data, 'copy'):
            data = data.copy()
        else:
            data = dict(data)
        if 'file' in data and 'image' not in data:
            data['image'] = data.pop('file')
        return super().to_internal_value(data)

    def validate_stock_quantity(self, value):
        if value is not None and value < 0:
            raise serializers.ValidationError("Stock quantity cannot be negative.")
        return value


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
    user_name = serializers.SerializerMethodField()
    fulfilled_by_email = serializers.ReadOnlyField(source='fulfilled_by.email')
    granted_package_name = serializers.ReadOnlyField(source='granted_package.package_type.name')

    class Meta:
        model = RewardRedemption
        fields = [
            'id', 'user', 'user_email', 'user_name', 'catalog_item', 'catalog_item_name',
            'points_spent', 'status', 'redemption_code', 'fulfilled_by_email',
            'fulfilled_at', 'granted_package', 'granted_package_name', 'notes', 'created_at'
        ]
        read_only_fields = [
            'id', 'user', 'user_email', 'user_name', 'catalog_item_name', 'points_spent',
            'redemption_code', 'fulfilled_by_email', 'fulfilled_at', 'granted_package',
            'granted_package_name', 'created_at'
        ]

    def get_user_name(self, obj):
        if obj.user:
            return obj.user.get_full_name() or obj.user.email
        return ""


class RedemptionCreateSerializer(serializers.Serializer):
    catalog_item_id = serializers.UUIDField(required=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class RedemptionCodeActionSerializer(serializers.Serializer):
    code = serializers.CharField(required=True, max_length=50, trim_whitespace=True)
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
