"""
Notification Engine — Django Admin
"""
from django.contrib import admin
from .models import (
    FCMDevice, NotificationTemplate, NotificationGroup, NotificationGroupMember,
    NotificationRecurrenceRule, NotificationCampaign, NotificationInbox,
    DeliveryRecord, NotificationPreference, NotificationAutomation,
    TenantNotificationSettings
)

@admin.register(FCMDevice)
class FCMDeviceAdmin(admin.ModelAdmin):
    list_display = ('user', 'tenant', 'platform', 'active', 'last_seen')
    list_filter = ('active', 'platform')
    search_fields = ('user__email', 'registration_id')
    raw_id_fields = ('user', 'tenant')

@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'notification_type', 'priority', 'is_active', 'is_critical')
    list_filter = ('is_active', 'notification_type', 'priority', 'is_critical')
    search_fields = ('name', 'title_template')
    raw_id_fields = ('tenant', 'created_by')

class NotificationGroupMemberInline(admin.TabularInline):
    model = NotificationGroupMember
    extra = 1
    raw_id_fields = ('user',)

@admin.register(NotificationGroup)
class NotificationGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'created_at')
    search_fields = ('name',)
    raw_id_fields = ('tenant', 'created_by')
    inlines = [NotificationGroupMemberInline]

@admin.register(NotificationRecurrenceRule)
class NotificationRecurrenceRuleAdmin(admin.ModelAdmin):
    list_display = ('id', 'tenant', 'start_date', 'end_date', 'send_time', 'is_active')
    list_filter = ('is_active',)
    raw_id_fields = ('tenant',)

@admin.register(NotificationCampaign)
class NotificationCampaignAdmin(admin.ModelAdmin):
    list_display = ('title', 'tenant', 'status', 'priority', 'delivery_policy', 'created_at')
    list_filter = ('status', 'priority', 'delivery_policy', 'source')
    search_fields = ('title', 'body')
    raw_id_fields = ('tenant', 'created_by', 'audience_group', 'recurrence_rule', 'template', 'audience_users')

class DeliveryRecordInline(admin.TabularInline):
    model = DeliveryRecord
    extra = 0
    raw_id_fields = ('device',)

@admin.register(NotificationInbox)
class NotificationInboxAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'title', 'tenant', 'is_read', 'push_sent', 'email_sent', 'created_at')
    list_filter = ('is_read', 'push_sent', 'email_sent')
    search_fields = ('recipient__email', 'title', 'body')
    raw_id_fields = ('recipient', 'tenant', 'campaign')
    inlines = [DeliveryRecordInline]

@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'push_enabled', 'email_enabled')
    list_filter = ('push_enabled', 'email_enabled')
    raw_id_fields = ('user',)

@admin.register(NotificationAutomation)
class NotificationAutomationAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'event_trigger', 'is_active')
    list_filter = ('is_active', 'event_trigger')
    search_fields = ('name',)
    raw_id_fields = ('tenant', 'template', 'created_by')

@admin.register(TenantNotificationSettings)
class TenantNotificationSettingsAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'timezone', 'quiet_hours_enabled', 'max_campaigns_per_day')
    list_filter = ('quiet_hours_enabled',)
    raw_id_fields = ('tenant',)
