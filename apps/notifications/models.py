from django.db import models
from django.conf import settings
from core_models.mixins.uuid_mixin import UUIDMixin
from core_models.mixins.timestamps import TimestampMixin
from django.utils.translation import gettext_lazy as _

class FCMDevice(UUIDMixin, TimestampMixin):
    """
    Stores Firebase Cloud Messaging device tokens for users.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='fcm_devices'
    )
    registration_id = models.TextField(_("Registration Token"))
    device_id = models.CharField(max_length=255, blank=True, null=True, help_text="Unique device identifier")
    device_type = models.CharField(max_length=50, blank=True, null=True, choices=[('ios', 'iOS'), ('android', 'Android'), ('web', 'Web')])
    active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("FCM Device")
        verbose_name_plural = _("FCM Devices")
        unique_together = ('user', 'registration_id')

    def __str__(self):
        return f"{self.user.email} - {self.device_type or 'Unknown Device'}"

class Notification(UUIDMixin, TimestampMixin):
    """
    Stores in-app notifications and status of push notifications sent.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='user_notifications'
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    data = models.JSONField(default=dict, blank=True, help_text="Custom payload data")
    
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    
    push_sent = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("Notification")
        verbose_name_plural = _("Notifications")
        ordering = ['-created_at']

    def __str__(self):
        return f"Notification for {self.user.email} - {self.title}"

