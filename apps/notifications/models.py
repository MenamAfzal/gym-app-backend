"""
Notification Engine Models

Production-grade multi-tenant notification system.

Architecture:
    NotificationCampaign    → owner-created broadcasts (immediate, scheduled, recurring)
    NotificationInbox       → per-user in-app history (persisted regardless of push delivery)
    DeliveryRecord          → per-device/channel delivery tracking
    NotificationTemplate    → reusable templates with variable substitution
    NotificationGroup       → tenant-level named user groups
    NotificationAutomation  → maps platform events to templates
    TenantNotificationSettings → quiet hours, timezone, rate limits
    NotificationPreference  → per-user opt-in/out by notification type
    FCMDevice               → Firebase device tokens (extended with tenant isolation)

Channel Selection Rule (enforced by ChannelPolicyEngine in services.py):
    CRITICAL priority OR template.is_critical=True  → PUSH_AND_EMAIL
    Everything else                                 → PUSH_ONLY

This is the ONLY place this decision is made. No view, task, or signal
independently decides to send email.
"""
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from core_models.mixins.uuid_mixin import UUIDMixin
from core_models.mixins.timestamps import TimestampMixin
from core_models.mixins.tenant_mixin import TenantMixin


# Choice Enumerations

class NotificationType(models.TextChoices):
    GENERAL      = 'GENERAL',      _('General')
    ANNOUNCEMENT = 'ANNOUNCEMENT', _('Announcement')
    CLASS        = 'CLASS',        _('Class')
    APPOINTMENT  = 'APPOINTMENT',  _('Appointment')
    BOOKING      = 'BOOKING',      _('Booking')
    WAITLIST     = 'WAITLIST',     _('Waitlist')
    WORKOUT      = 'WORKOUT',      _('Workout')
    MEMBERSHIP   = 'MEMBERSHIP',   _('Membership')
    PAYMENT      = 'PAYMENT',      _('Payment')
    NUTRITION    = 'NUTRITION',    _('Nutrition')
    INVENTORY    = 'INVENTORY',    _('Inventory')
    SOCIAL       = 'SOCIAL',       _('Social')
    MAINTENANCE  = 'MAINTENANCE',  _('Maintenance')
    EMERGENCY    = 'EMERGENCY',    _('Emergency')
    SYSTEM       = 'SYSTEM',       _('System')


class NotificationPriority(models.TextChoices):
    LOW      = 'LOW',      _('Low')
    NORMAL   = 'NORMAL',   _('Normal')
    HIGH     = 'HIGH',     _('High')
    CRITICAL = 'CRITICAL', _('Critical')


class DeliveryPolicy(models.TextChoices):
    """
    Derived channel policy — computed by ChannelPolicyEngine, NEVER directly user-settable.

    PUSH_ONLY     → normal/low/high priority without explicit critical flag
    PUSH_AND_EMAIL → CRITICAL priority or template.is_critical=True
    """
    PUSH_ONLY      = 'PUSH_ONLY',      _('Push Only')
    PUSH_AND_EMAIL = 'PUSH_AND_EMAIL', _('Push and Email')


class NotificationStatus(models.TextChoices):
    DRAFT          = 'DRAFT',          _('Draft')
    SCHEDULED      = 'SCHEDULED',      _('Scheduled')
    PROCESSING     = 'PROCESSING',     _('Processing')
    SENT           = 'SENT',           _('Sent')
    PARTIALLY_SENT = 'PARTIALLY_SENT', _('Partially Sent')
    FAILED         = 'FAILED',         _('Failed')
    CANCELLED      = 'CANCELLED',      _('Cancelled')


class NotificationSource(models.TextChoices):
    """
    Distinguishes the origin of a notification for history/analytics/debugging.
    """
    SYSTEM     = 'SYSTEM',     _('System')      # Direct platform events (payment failed, booking)
    CAMPAIGN   = 'CAMPAIGN',   _('Campaign')    # Owner-created broadcast
    AUTOMATION = 'AUTOMATION', _('Automation')  # Automation rule triggered


class NotificationAudienceType(models.TextChoices):
    ALL_CLIENTS          = 'ALL_CLIENTS',          _('All Clients')
    ALL_STAFF            = 'ALL_STAFF',            _('All Staff')
    ALL_TRAINERS         = 'ALL_TRAINERS',         _('All Trainers')
    ALL_MANAGERS         = 'ALL_MANAGERS',         _('All Managers')
    SPECIFIC_USERS       = 'SPECIFIC_USERS',       _('Specific Users')
    GROUP                = 'GROUP',                _('Notification Group')
    CLASS_BOOKINGS       = 'CLASS_BOOKINGS',       _('Class Bookings')
    CLASS_WAITLIST       = 'CLASS_WAITLIST',       _('Class Waitlist')
    TRAINER_CLIENTS      = 'TRAINER_CLIENTS',      _('Trainer Clients')
    APPOINTMENT_ATTENDEES = 'APPOINTMENT_ATTENDEES', _('Appointment Attendees')
    DYNAMIC_FILTER       = 'DYNAMIC_FILTER',       _('Dynamic Filter')


class AutomationEventTrigger(models.TextChoices):
    BOOKING_CONFIRMED    = 'booking_confirmed',    _('Booking Confirmed')
    BOOKING_CANCELLED    = 'booking_cancelled',    _('Booking Cancelled')
    WAITLIST_OFFERED     = 'waitlist_offered',     _('Waitlist Spot Offered')
    APPOINTMENT_REMINDER = 'appointment_reminder', _('Appointment Reminder')
    WORKOUT_ASSIGNED     = 'workout_assigned',     _('Workout Assigned')
    PAYMENT_SUCCESS      = 'payment_success',      _('Payment Successful')
    PAYMENT_FAILED       = 'payment_failed',       _('Payment Failed')
    MEMBERSHIP_EXPIRING  = 'membership_expiring',  _('Membership Expiring')
    CLASS_REMINDER_24H   = 'class_reminder_24h',   _('Class Reminder (24h)')
    CLASS_REMINDER_1H    = 'class_reminder_1h',    _('Class Reminder (1h)')
    SESSION_CANCELLED    = 'session_cancelled',    _('Session Cancelled')
    SUBSTITUTE_REQUEST_BROADCAST = 'substitute_request_broadcast', _('Substitute Request Broadcast')


# FCM Device (extended with tenant isolation)

class FCMDevice(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Firebase Cloud Messaging device token for a user.
    Users can have multiple devices (iPhone, Android, tablet).
    Tenant-scoped: a device belongs to exactly one gym.
    """
    PLATFORM_CHOICES = [
        ('ios',     'iOS'),
        ('android', 'Android'),
        ('web',     'Web'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='fcm_devices',
    )
    registration_id = models.TextField(
        _('Registration Token'),
        help_text="Firebase device registration token"
    )
    device_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Unique device identifier (optional)"
    )
    platform = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=PLATFORM_CHOICES,
        help_text="Device platform"
    )
    active = models.BooleanField(
        default=True,
        help_text="Whether this token is still valid. Set to False on UNREGISTERED error."
    )
    last_seen = models.DateTimeField(
        auto_now=True,
        help_text="Last time this device was seen (auto-updated on save)"
    )

    class Meta:
        verbose_name = _('FCM Device')
        verbose_name_plural = _('FCM Devices')
        unique_together = ('user', 'registration_id')
        indexes = [
            models.Index(fields=['tenant', 'user', 'active']),
        ]

    def __str__(self):
        return f"{self.user.email} — {self.platform or 'Unknown'} ({'active' if self.active else 'inactive'})"


# Notification Template

class NotificationTemplate(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Reusable notification template with variable substitution.

    Supported variables: {{client_name}}, {{gym_name}}, {{trainer_name}},
    {{class_name}}, {{class_time}}, {{room_name}}, {{package_name}},
    {{expiry_date}}, {{booking_date}}, {{appointment_time}}, {{days_remaining}}

    is_critical=True forces PUSH_AND_EMAIL delivery regardless of priority.
    is_user_configurable=False means user preference opt-out is ignored (mandatory delivery).
    """
    name              = models.CharField(max_length=255)
    notification_type = models.CharField(max_length=20, choices=NotificationType.choices)
    title_template    = models.CharField(max_length=255, help_text="Supports {{variable}} substitution")
    body_template     = models.TextField(help_text="Supports {{variable}} substitution")
    priority          = models.CharField(
        max_length=10,
        choices=NotificationPriority.choices,
        default=NotificationPriority.NORMAL,
    )

    # Channel policy flag
    is_critical = models.BooleanField(
        default=False,
        help_text="If True, forces PUSH_AND_EMAIL delivery. Set for payment failures, emergencies."
    )

    # User preference flag
    is_user_configurable = models.BooleanField(
        default=True,
        help_text="If False, user preference opt-out is ignored. Use for mandatory notifications."
    )

    is_active  = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_notification_templates',
    )

    class Meta:
        verbose_name        = _('Notification Template')
        verbose_name_plural = _('Notification Templates')
        indexes = [
            models.Index(fields=['tenant', 'notification_type', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} ({self.notification_type})"


# Notification Group

class NotificationGroup(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Tenant-level named group of users for audience targeting.
    Examples: "Morning Members", "VIP Members", "CrossFit Team"
    """
    name        = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_notification_groups',
    )

    class Meta:
        verbose_name        = _('Notification Group')
        verbose_name_plural = _('Notification Groups')
        unique_together = ('tenant', 'name')

    def __str__(self):
        return f"{self.name} ({self.tenant.name if self.tenant_id else 'No Tenant'})"


class NotificationGroupMember(UUIDMixin, TimestampMixin):
    """
    Membership link between a NotificationGroup and a User.
    Service-layer enforces: group.tenant == user.tenant before adding.
    """
    group = models.ForeignKey(
        NotificationGroup,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_group_memberships',
    )

    class Meta:
        verbose_name        = _('Notification Group Member')
        verbose_name_plural = _('Notification Group Members')
        unique_together = ('group', 'user')

    def __str__(self):
        return f"{self.user.email} in {self.group.name}"


# Notification Recurrence Rule

class NotificationRecurrenceRule(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Recurring schedule for a notification campaign.

    Mirrors scheduling.RecurrenceRule schema (same days_of_week JSON structure,
    same start_date/end_date/time pattern) for consistency.
    Same processing logic — no second rule engine.
    """
    days_of_week = models.JSONField(
        help_text="List of weekday strings, e.g. ['monday', 'wednesday', 'friday']"
    )
    start_date = models.DateField()
    end_date   = models.DateField()
    send_time  = models.TimeField(
        help_text="Time of day to send, in the tenant's configured timezone"
    )
    is_active  = models.BooleanField(default=True)

    class Meta:
        verbose_name        = _('Notification Recurrence Rule')
        verbose_name_plural = _('Notification Recurrence Rules')

    def __str__(self):
        days = ', '.join(self.days_of_week) if isinstance(self.days_of_week, list) else str(self.days_of_week)
        return f"{days} at {self.send_time} ({self.start_date} → {self.end_date})"


# Notification Campaign (main broadcast model)

class NotificationCampaign(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Owner-created notification broadcast.

    Supports:
    - Immediate send
    - Scheduled send (specific datetime in tenant timezone)
    - Recurring send (via NotificationRecurrenceRule)

    Channel selection is DERIVED by ChannelPolicyEngine — never directly user-settable.
    The owner controls: who, what, when, and why.
    The engine controls: push-only vs push+email.
    """

    title             = models.CharField(max_length=255)
    body              = models.TextField()
    notification_type = models.CharField(max_length=20, choices=NotificationType.choices)
    priority          = models.CharField(
        max_length=10,
        choices=NotificationPriority.choices,
        default=NotificationPriority.NORMAL,
    )
    source = models.CharField(
        max_length=15,
        choices=NotificationSource.choices,
        default=NotificationSource.CAMPAIGN,
        help_text="Origin: CAMPAIGN (owner-created), AUTOMATION (rule-triggered), SYSTEM (platform event)",
    )

    delivery_policy = models.CharField(
        max_length=15,
        choices=DeliveryPolicy.choices,
        default=DeliveryPolicy.PUSH_ONLY,
        help_text="Derived by ChannelPolicyEngine from priority and template. Not directly user-settable.",
    )

    status = models.CharField(
        max_length=15,
        choices=NotificationStatus.choices,
        default=NotificationStatus.DRAFT,
        db_index=True,
    )

    audience_type = models.CharField(
        max_length=25,
        choices=NotificationAudienceType.choices,
        default=NotificationAudienceType.ALL_CLIENTS,
    )
    audience_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='targeted_campaigns',
        help_text="Used when audience_type=SPECIFIC_USERS",
    )
    audience_group = models.ForeignKey(
        NotificationGroup,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='campaigns',
        help_text="Used when audience_type=GROUP",
    )
    audience_entity_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="Entity ID for CLASS_BOOKINGS, CLASS_WAITLIST, TRAINER_CLIENTS, APPOINTMENT_ATTENDEES",
    )
    audience_filter = models.JSONField(
        default=dict,
        blank=True,
        help_text="Dynamic filter config for DYNAMIC_FILTER audience type",
    )

    scheduled_at     = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Scheduled delivery time (UTC). null = immediate on send().",
    )
    recurrence_rule  = models.ForeignKey(
        NotificationRecurrenceRule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='campaigns',
    )
    next_run_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Next scheduled execution time (UTC). Updated after each recurring run.",
    )

    bypass_quiet_hours = models.BooleanField(
        default=False,
        help_text="If True, ignores quiet hours. CRITICAL always bypasses if tenant setting allows.",
    )

    action_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text='Structured deep-link payload. e.g. {"action": "OPEN_WORKOUT", "entity_type": "workout", "entity_id": "123"}',
    )

    template = models.ForeignKey(
        NotificationTemplate,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='campaigns',
    )

    recipient_count   = models.PositiveIntegerField(default=0)
    push_sent_count   = models.PositiveIntegerField(default=0)
    push_failed_count = models.PositiveIntegerField(default=0)
    email_sent_count  = models.PositiveIntegerField(default=0)
    email_failed_count = models.PositiveIntegerField(default=0)
    processed_at      = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_campaigns',
    )
    celery_task_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Celery task ID for potential cancellation via revoke()",
    )
    idempotency_key = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Prevents duplicate campaign delivery on Celery retries",
    )

    class Meta:
        verbose_name        = _('Notification Campaign')
        verbose_name_plural = _('Notification Campaigns')
        ordering            = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'source']),
            models.Index(fields=['tenant', 'notification_type']),
            models.Index(fields=['next_run_at', 'status']),
            models.Index(fields=['tenant', 'created_at']),
        ]

    def __str__(self):
        return f"{self.title} [{self.status}] — {self.tenant.name if self.tenant_id else 'No Tenant'}"


# Notification Inbox (per-user in-app notification center)

class NotificationInbox(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Per-user in-app notification record.

    Persisted regardless of push delivery success.
    Push notifications are transient — this is the reliable notification history.

    Replaces both:
    - apps/notifications/Notification (old thin model, no tenant)
    - apps/scheduling/Notification (inbox buffer that never delivered)
    """

    campaign  = models.ForeignKey(
        NotificationCampaign,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='inbox_items',
        help_text="Source campaign, if this was a broadcast. Null for automated/system notifications.",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_inbox',
    )

    # Content (resolved at creation time — snapshot, not live from template)
    title             = models.CharField(max_length=255)
    body              = models.TextField()
    notification_type = models.CharField(max_length=20, choices=NotificationType.choices)
    priority          = models.CharField(max_length=10, choices=NotificationPriority.choices)
    source            = models.CharField(max_length=15, choices=NotificationSource.choices, default=NotificationSource.CAMPAIGN)
    delivery_policy   = models.CharField(max_length=15, choices=DeliveryPolicy.choices, default=DeliveryPolicy.PUSH_ONLY)
    action_payload    = models.JSONField(default=dict, blank=True)

    # Read state
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    # Delivery flags (updated by NotificationDispatcher)
    push_sent  = models.BooleanField(default=False)
    email_sent = models.BooleanField(default=False)

    # Idempotency key — prevents duplicate notifications on Celery retries
    # Pattern: "campaign:{campaign_id}:user:{user_id}" or "{event_type}:{recipient_id}:{entity_id}"
    idempotency_key = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )

    class Meta:
        verbose_name        = _('Notification Inbox Item')
        verbose_name_plural = _('Notification Inbox Items')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read']),
            models.Index(fields=['tenant', 'recipient', 'created_at']),
            models.Index(fields=['tenant', 'source']),
            models.Index(fields=['tenant', 'notification_type']),
        ]

    def __str__(self):
        return f"[{self.notification_type}] {self.title} → {self.recipient.email}"


# Delivery Record (per-device/channel tracking)

class DeliveryRecord(UUIDMixin, TimestampMixin):
    """
    Tracks individual delivery attempts per device (for Push) or per recipient (for Email).

    Push delivery:  device FK is set, email_address is empty
    Email delivery: device FK is NULL, email_address is set

    Provider response is stored as structured fields (not a raw blob) for queryability.
    provider_response_raw is for debugging only and should be pruned by data retention policy.
    """
    CHANNEL_CHOICES = [('push', 'Push'), ('email', 'Email')]
    STATUS_CHOICES  = [('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')]

    inbox_item = models.ForeignKey(
        NotificationInbox,
        on_delete=models.CASCADE,
        related_name='delivery_records',
    )

    # Channel
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)

    # Push: device is set. Email: device is NULL.
    device        = models.ForeignKey(
        FCMDevice,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='delivery_records',
    )
    email_address = models.EmailField(blank=True, help_text="Set for email channel delivery records")

    # Delivery status
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)

    # Structured provider response (not a raw blob)
    provider_message_id = models.CharField(max_length=255, blank=True, help_text="Provider's message/delivery ID")
    provider_status     = models.CharField(max_length=100, blank=True, help_text="Provider-reported status")
    error_code          = models.CharField(max_length=100, blank=True, help_text="Error code on failure (e.g. UNREGISTERED)")
    error_message       = models.TextField(blank=True, help_text="Human-readable error on failure")

    # Raw response for debugging (should be pruned by data retention policy)
    provider_response_raw = models.JSONField(
        default=dict,
        blank=True,
        help_text="Raw provider response. For debugging only — prune according to retention policy.",
    )

    attempted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = _('Delivery Record')
        verbose_name_plural = _('Delivery Records')
        indexes = [
            models.Index(fields=['inbox_item', 'channel', 'status']),
        ]

    def __str__(self):
        target = self.device.registration_id[:20] if self.device else self.email_address
        return f"{self.channel} → {target} [{self.status}]"


# Notification Preference (per-user opt-in/out)

class NotificationPreference(UUIDMixin, TimestampMixin):
    """
    Per-user notification preferences.

    User.tenant is a ForeignKey (one tenant per user), so OneToOneField(User) is
    tenant-safe — no separate tenant-level scoping required.

    Note: preferences with template.is_user_configurable=False are ignored here —
    those notifications are always delivered (emergency, security, payment failures).
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_preference',
    )

    # Per-type enabled flags — JSON for extensibility without schema changes
    # Example: {"CLASS": true, "WORKOUT": true, "SOCIAL": false, "ANNOUNCEMENT": true}
    preferences = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-notification-type opt-in state. Keys are NotificationType values.",
    )

    push_enabled  = models.BooleanField(default=True, help_text="User-level push channel toggle")
    email_enabled = models.BooleanField(default=True, help_text="User-level email channel toggle")

    class Meta:
        verbose_name        = _('Notification Preference')
        verbose_name_plural = _('Notification Preferences')

    def __str__(self):
        return f"Preferences for {self.user.email}"

    def is_type_enabled(self, notification_type: str) -> bool:
        """Returns True if this notification type is enabled for the user."""
        return self.preferences.get(notification_type, True)  # Default: all types enabled

    @classmethod
    def get_or_create_for_user(cls, user):
        """Get or create preferences with all types enabled by default."""
        pref, _ = cls.objects.get_or_create(user=user)
        return pref


# Notification Automation

class NotificationAutomation(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Maps a platform event trigger to a NotificationTemplate for automated delivery.

    When a trigger fires (e.g. 'booking_confirmed'), the engine:
    1. Looks up the active automation for this tenant + trigger
    2. Renders the template with event context_data
    3. Creates NotificationInbox + dispatches via NotificationDispatcher

    If no automation is configured for a trigger, the engine uses system defaults.
    """
    name          = models.CharField(max_length=255)
    event_trigger = models.CharField(
        max_length=30,
        choices=AutomationEventTrigger.choices,
        db_index=True,
    )
    template     = models.ForeignKey(
        NotificationTemplate,
        on_delete=models.PROTECT,
        related_name='automations',
    )
    is_active     = models.BooleanField(default=True)
    lead_time_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="For time-based reminders: minutes before event to send. e.g. 60 = 1 hour before.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_automations',
    )

    class Meta:
        verbose_name        = _('Notification Automation')
        verbose_name_plural = _('Notification Automations')
        # One active automation per trigger per tenant
        unique_together = ('tenant', 'event_trigger')
        indexes = [
            models.Index(fields=['tenant', 'event_trigger', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} [{self.event_trigger}] — {'active' if self.is_active else 'inactive'}"


# Tenant Notification Settings

class TenantNotificationSettings(UUIDMixin, TimestampMixin):
    """
    Tenant-level notification configuration.

    Quiet hours: normal notifications scheduled during quiet window are delayed
    until the window ends. CRITICAL notifications bypass quiet hours (if configured).
    """
    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='notification_settings',
    )

    # Timezone for scheduled notification interpretation
    timezone = models.CharField(
        max_length=100,
        default='UTC',
        help_text="Tenant timezone for scheduled notifications (e.g. 'Asia/Karachi')",
    )

    # Quiet hours
    quiet_hours_enabled         = models.BooleanField(default=False)
    quiet_hours_start           = models.TimeField(null=True, blank=True, help_text="Start of quiet window (e.g. 22:00)")
    quiet_hours_end             = models.TimeField(null=True, blank=True, help_text="End of quiet window (e.g. 07:00)")
    quiet_hours_bypass_critical = models.BooleanField(
        default=True,
        help_text="If True, CRITICAL priority notifications bypass quiet hours",
    )

    # Rate limiting (basic guard against accidental notification explosions)
    max_campaigns_per_day = models.PositiveIntegerField(
        default=50,
        help_text="Maximum campaigns a tenant can send per day",
    )

    class Meta:
        verbose_name        = _('Tenant Notification Settings')
        verbose_name_plural = _('Tenant Notification Settings')

    def __str__(self):
        return f"Notification Settings for {self.tenant.name}"

    @classmethod
    def get_or_create_for_tenant(cls, tenant):
        settings_obj, _ = cls.objects.get_or_create(tenant=tenant)
        return settings_obj
