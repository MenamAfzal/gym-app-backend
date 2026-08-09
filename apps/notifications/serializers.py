"""
Notification Engine — Serializers

Covers all endpoints:
- Campaign management (CRUD + send/cancel actions)
- Templates
- Automations
- Groups + member management
- Inbox (read-only for recipient)
- Device registration/removal
- User preferences
- Tenant settings
"""
from rest_framework import serializers
from django.utils import timezone
from .models import (
    FCMDevice, NotificationTemplate, NotificationGroup, NotificationGroupMember,
    NotificationRecurrenceRule, NotificationCampaign, NotificationInbox,
    DeliveryRecord, NotificationPreference, NotificationAutomation,
    TenantNotificationSettings, NotificationType, NotificationPriority,
    NotificationStatus, NotificationAudienceType, DeliveryPolicy,
    NotificationSource, AutomationEventTrigger,
)


# FCM Device

class FCMDeviceRegisterSerializer(serializers.Serializer):
    """Used for device registration (upsert by registration_id)."""
    registration_id = serializers.CharField(required=True)
    device_id       = serializers.CharField(required=False, allow_blank=True, default='')
    platform        = serializers.ChoiceField(
        choices=['ios', 'android', 'web'],
        required=False,
        allow_blank=True,
        allow_null=True,
        default=None,
    )


class FCMDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FCMDevice
        fields = ['id', 'registration_id', 'device_id', 'platform', 'active', 'last_seen', 'created_at']
        read_only_fields = ['id', 'active', 'last_seen', 'created_at']


# Notification Template

class NotificationTemplateSerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source='created_by.email', read_only=True, default=None)

    class Meta:
        model = NotificationTemplate
        fields = [
            'id', 'name', 'notification_type', 'title_template', 'body_template',
            'priority', 'is_critical', 'is_user_configurable', 'is_active',
            'created_by', 'created_by_email', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'created_by', 'created_by_email', 'created_at', 'updated_at',
            'is_critical', 'is_user_configurable'
        ]


class NotificationTemplateListSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationTemplate
        fields = ['id', 'name', 'notification_type', 'priority', 'is_critical', 'is_active', 'created_at']


# Notification Group

class NotificationGroupSerializer(serializers.ModelSerializer):
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = NotificationGroup
        fields = ['id', 'name', 'description', 'member_count', 'created_by', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_by', 'member_count', 'created_at', 'updated_at']

    def get_member_count(self, obj):
        return obj.memberships.count()


class GroupMemberAddSerializer(serializers.Serializer):
    user_id = serializers.UUIDField(required=True)

    def validate_user_id(self, value):
        from apps.users.models import User
        request = self.context.get('request')
        group   = self.context.get('group')
        try:
            user = User.objects.get(id=value)
        except User.DoesNotExist:
            raise serializers.ValidationError("User not found.")

        if group and user.tenant_id != group.tenant_id:
            raise serializers.ValidationError("User does not belong to this tenant.")
        return value


class GroupMemberRemoveSerializer(serializers.Serializer):
    user_id = serializers.UUIDField(required=True)


# Notification Recurrence Rule

class NotificationRecurrenceRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model  = NotificationRecurrenceRule
        fields = ['days_of_week', 'start_date', 'end_date', 'send_time']

    def validate_days_of_week(self, value):
        valid_days = {'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'}
        if not isinstance(value, list):
            raise serializers.ValidationError("days_of_week must be a list.")
        invalid = [d for d in value if d.lower() not in valid_days]
        if invalid:
            raise serializers.ValidationError(f"Invalid day values: {invalid}")
        return [d.lower() for d in value]

    def validate(self, data):
        if data.get('start_date') and data.get('end_date'):
            if data['start_date'] > data['end_date']:
                raise serializers.ValidationError("start_date must be before end_date.")
        return data


# Notification Campaign

class CampaignCreateSerializer(serializers.ModelSerializer):
    """
    Used for creating campaigns (POST /campaigns/).

    Note: delivery_policy is NOT an input field.
    It is computed by ChannelPolicyEngine.derive_policy() in NotificationService.create_campaign().
    """
    recurrence_rule_data = NotificationRecurrenceRuleSerializer(required=False, allow_null=True, write_only=True)
    audience_users       = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    audience_filter      = serializers.JSONField(required=False, allow_null=True, default=dict)

    class Meta:
        model  = NotificationCampaign
        fields = [
            'title', 'body', 'notification_type', 'priority',
            'audience_type', 'audience_users', 'audience_group',
            'audience_entity_id', 'audience_filter',
            'scheduled_at', 'recurrence_rule_data',
            'bypass_quiet_hours', 'action_payload', 'template',
        ]

    def validate(self, data):
        audience_type = data.get('audience_type')

        if audience_type == NotificationAudienceType.SPECIFIC_USERS:
            if not data.get('audience_users'):
                raise serializers.ValidationError({'audience_users': 'Required for SPECIFIC_USERS audience.'})

        if audience_type == NotificationAudienceType.GROUP:
            if not data.get('audience_group'):
                raise serializers.ValidationError({'audience_group': 'Required for GROUP audience.'})

        entity_types = {
            NotificationAudienceType.CLASS_BOOKINGS,
            NotificationAudienceType.CLASS_WAITLIST,
            NotificationAudienceType.TRAINER_CLIENTS,
            NotificationAudienceType.APPOINTMENT_ATTENDEES,
        }
        if audience_type in entity_types and not data.get('audience_entity_id'):
            raise serializers.ValidationError({'audience_entity_id': f'Required for {audience_type} audience.'})

        if audience_type == NotificationAudienceType.DYNAMIC_FILTER:
            filter_data = data.get('audience_filter', {})
            filter_serializer = DynamicAudienceFilterSerializer(data=filter_data)
            if not filter_serializer.is_valid():
                raise serializers.ValidationError({'audience_filter': filter_serializer.errors})
            data['audience_filter'] = filter_serializer.validated_data

        return data

class DynamicAudienceFilterSerializer(serializers.Serializer):
    membership_expiring_within_days = serializers.IntegerField(required=False, min_value=1, allow_null=True)
    assigned_trainer                = serializers.UUIDField(required=False, allow_null=True)
    booked_today                    = serializers.BooleanField(required=False, allow_null=True)
    active_members                  = serializers.BooleanField(required=False, allow_null=True)
    last_login_older_than_days      = serializers.IntegerField(required=False, min_value=1, allow_null=True)


class CampaignReadSerializer(serializers.ModelSerializer):
    delivery_policy_display = serializers.CharField(source='get_delivery_policy_display', read_only=True)
    status_display          = serializers.CharField(source='get_status_display', read_only=True)
    created_by_email        = serializers.EmailField(source='created_by.email', read_only=True, default=None)
    template_name           = serializers.CharField(source='template.name', read_only=True, default=None)
    recurrence_rule         = NotificationRecurrenceRuleSerializer(read_only=True)

    class Meta:
        model  = NotificationCampaign
        fields = [
            'id', 'title', 'body', 'notification_type', 'priority', 'status', 'status_display',
            'source', 'delivery_policy', 'delivery_policy_display',
            'audience_type', 'audience_entity_id', 'audience_filter',
            'scheduled_at', 'recurrence_rule', 'next_run_at',
            'bypass_quiet_hours', 'action_payload', 'template', 'template_name',
            'recipient_count', 'push_sent_count', 'push_failed_count',
            'email_sent_count', 'email_failed_count',
            'processed_at', 'created_by', 'created_by_email', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class CampaignUpdateSerializer(serializers.ModelSerializer):
    """Allows updating only DRAFT campaigns."""
    recurrence_rule_data = NotificationRecurrenceRuleSerializer(required=False, allow_null=True, write_only=True)

    class Meta:
        model  = NotificationCampaign
        fields = [
            'title', 'body', 'notification_type', 'priority',
            'audience_type', 'audience_entity_id', 'audience_filter',
            'scheduled_at', 'recurrence_rule_data', 'bypass_quiet_hours',
            'action_payload', 'template',
        ]

    def validate(self, data):
        instance = self.instance
        if instance and instance.status != NotificationStatus.DRAFT:
            raise serializers.ValidationError("Only DRAFT campaigns can be updated.")
            
        audience_type = data.get('audience_type', getattr(instance, 'audience_type', None))
        if audience_type == NotificationAudienceType.DYNAMIC_FILTER and 'audience_filter' in data:
            filter_data = data['audience_filter']
            if filter_data is not None:
                filter_serializer = DynamicAudienceFilterSerializer(data=filter_data)
                if not filter_serializer.is_valid():
                    raise serializers.ValidationError({'audience_filter': filter_serializer.errors})
                data['audience_filter'] = filter_serializer.validated_data
            
        return data


# Notification Inbox

class DeliveryRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model  = DeliveryRecord
        fields = [
            'id', 'channel', 'status', 'email_address',
            'provider_message_id', 'provider_status',
            'error_code', 'error_message', 'attempted_at',
        ]


class NotificationInboxSerializer(serializers.ModelSerializer):
    delivery_records = DeliveryRecordSerializer(many=True, read_only=True)

    class Meta:
        model  = NotificationInbox
        fields = [
            'id', 'title', 'body', 'notification_type', 'priority',
            'source', 'delivery_policy', 'action_payload',
            'is_read', 'read_at', 'push_sent', 'email_sent',
            'delivery_records', 'created_at',
        ]
        read_only_fields = fields


class NotificationInboxListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list view (no delivery records)."""
    class Meta:
        model  = NotificationInbox
        fields = [
            'id', 'title', 'body', 'notification_type', 'priority',
            'source', 'action_payload', 'is_read', 'created_at',
        ]
        read_only_fields = fields


# Notification Preference

class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model  = NotificationPreference
        fields = ['id', 'preferences', 'push_enabled', 'email_enabled']
        read_only_fields = ['id']

    def validate_preferences(self, value):
        valid_types = {c[0] for c in NotificationType.choices}
        invalid = {k for k in value if k not in valid_types}
        if invalid:
            raise serializers.ValidationError(f"Invalid notification type keys: {invalid}")
        return value


# Notification Automation

class NotificationAutomationSerializer(serializers.ModelSerializer):
    template_name     = serializers.CharField(source='template.name', read_only=True)
    created_by_email  = serializers.EmailField(source='created_by.email', read_only=True, default=None)

    class Meta:
        model  = NotificationAutomation
        fields = [
            'id', 'name', 'event_trigger', 'template', 'template_name',
            'is_active', 'lead_time_minutes', 'created_by', 'created_by_email',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_by_email', 'created_at', 'updated_at']

    def validate(self, data):
        request = self.context.get('request')
        event_trigger = data.get('event_trigger', getattr(self.instance, 'event_trigger', None))
        tenant = request.tenant

        # Enforce unique_together (one automation per trigger per tenant)
        qs = NotificationAutomation.objects.filter(tenant=tenant, event_trigger=event_trigger)
        if self.instance:
            qs = qs.exclude(id=self.instance.id)
        if qs.exists():
            raise serializers.ValidationError(
                {'event_trigger': f"An active automation for '{event_trigger}' already exists for this tenant."}
            )
        return data


# Tenant Notification Settings

class TenantNotificationSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model  = TenantNotificationSettings
        fields = [
            'id', 'timezone', 'quiet_hours_enabled',
            'quiet_hours_start', 'quiet_hours_end',
            'quiet_hours_bypass_critical', 'max_campaigns_per_day',
        ]
        read_only_fields = ['id']

    def validate(self, data):
        if data.get('quiet_hours_enabled'):
            if not data.get('quiet_hours_start') and not (self.instance and self.instance.quiet_hours_start):
                raise serializers.ValidationError({'quiet_hours_start': 'Required when quiet_hours_enabled is True.'})
            if not data.get('quiet_hours_end') and not (self.instance and self.instance.quiet_hours_end):
                raise serializers.ValidationError({'quiet_hours_end': 'Required when quiet_hours_enabled is True.'})
        return data
