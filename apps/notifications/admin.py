from django.contrib import admin
from .models import FCMDevice, Notification

@admin.register(FCMDevice)
class FCMDeviceAdmin(admin.ModelAdmin):
    list_display = ('user', 'device_type', 'active', 'created_at')
    list_filter = ('active', 'device_type')
    search_fields = ('user__email', 'registration_id', 'device_id')

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('user', 'title', 'is_read', 'push_sent', 'created_at')
    list_filter = ('is_read', 'push_sent', 'created_at')
    search_fields = ('user__email', 'title', 'body')
