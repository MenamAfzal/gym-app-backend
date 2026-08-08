from rest_framework import serializers
from .models import FCMDevice, Notification

class FCMDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = FCMDevice
        fields = ['registration_id', 'device_id', 'device_type', 'active']
        extra_kwargs = {
            'registration_id': {'required': True},
        }

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'title', 'body', 'data', 'is_read', 'read_at', 'created_at']
        read_only_fields = ['id', 'title', 'body', 'data', 'is_read', 'read_at', 'created_at']
