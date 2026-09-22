from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError
from core_models.mixins.uuid_mixin import UUIDMixin
from core_models.mixins.timestamps import TimestampMixin
from core_models.mixins.tenant_mixin import TenantMixin
from django.utils import timezone
from datetime import timedelta

User = settings.AUTH_USER_MODEL

class Location(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Top-level tenant scoping for gym locations.
    """
    name = models.CharField(max_length=255)
    address = models.TextField()
    timezone = models.CharField(max_length=100, default='UTC', help_text="Timezone name, e.g. 'America/New_York'")
    phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"{self.name} ({self.tenant.name if self.tenant else 'No Tenant'})"


from core_models.mixins.soft_delete import SoftDeleteModel, TenantSoftDeleteManager


class Room(UUIDMixin, TimestampMixin, TenantMixin, SoftDeleteModel):
    """
    Rooms within a specific Location. Used for layout definitions and conflict checking.
    """
    ROOM_TYPES = [
        ('class', 'Class'),
        ('appointment', 'Appointment'),
        ('facility', 'Facility'),
    ]

    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='rooms')
    name = models.CharField(max_length=120)
    room_type = models.CharField(max_length=20, choices=ROOM_TYPES, default='class')
    capacity = models.PositiveIntegerField(default=0, help_text="Configured or default room capacity")
    default_capacity = models.PositiveIntegerField(default=0, help_text="Default class capacity fallback")
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='rooms/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    equipment_tags = models.JSONField(default=list, blank=True, help_text="List of equipment tags")

    objects = TenantSoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=['location', 'is_deleted']),
        ]

    def save(self, *args, **kwargs):
        if not self.default_capacity and self.capacity:
            self.default_capacity = self.capacity
        elif not self.capacity and self.default_capacity:
            self.capacity = self.default_capacity
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} - {self.location.name}"


class SpotType(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Categories of spots/equipment per Location (e.g. Bike, Mat, Reformer, Instructor Stage).
    Maximum of 20 spot types allowed per location.
    """
    MAX_PER_LOCATION = 20

    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='spot_types')
    name = models.CharField(max_length=60)
    prefix = models.CharField(max_length=6, help_text="Prefix for spot labels, e.g. 'B' -> B1, B2")
    is_bookable = models.BooleanField(default=True)
    color = models.CharField(max_length=7, default='#6366F1')

    class Meta:
        unique_together = ['location', 'name']

    def clean(self):
        super().clean()
        if not self.pk:
            count = SpotType.objects.filter(location=self.location).count()
            if count >= self.MAX_PER_LOCATION:
                raise ValidationError(f"Maximum of {self.MAX_PER_LOCATION} spot types allowed per location.")

    def __str__(self):
        return f"{self.name} ({self.prefix}) - {self.location.name}"


class RoomLayout(UUIDMixin, TimestampMixin, TenantMixin, SoftDeleteModel):
    """
    2D floor plan layout configuration for a Room.
    """
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='layouts')
    name = models.CharField(max_length=120)
    grid_rows = models.PositiveSmallIntegerField()
    grid_cols = models.PositiveSmallIntegerField()
    version = models.PositiveIntegerField(default=1)

    objects = TenantSoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        indexes = [
            models.Index(fields=['room', 'is_deleted']),
        ]

    @property
    def capacity(self):
        """
        Computed capacity derived strictly from active, bookable spots.
        """
        return self.spots.filter(spot_type__is_bookable=True).count()

    def __str__(self):
        return f"{self.name} (v{self.version}) - {self.room.name}"


class Spot(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Individually addressable spot on a RoomLayout 2D grid.
    """
    layout = models.ForeignKey(RoomLayout, on_delete=models.CASCADE, related_name='spots')
    spot_type = models.ForeignKey(SpotType, on_delete=models.PROTECT, related_name='spots')
    row = models.PositiveSmallIntegerField()
    col = models.PositiveSmallIntegerField()
    number = models.PositiveIntegerField(help_text="Stable sequential number per spot type within layout")
    label = models.CharField(max_length=16, editable=False)
    is_blocked = models.BooleanField(default=False, help_text="Layout-level block")

    class Meta:
        unique_together = [
            ('layout', 'row', 'col'),
            ('layout', 'spot_type', 'number')
        ]
        indexes = [
            models.Index(fields=['layout', 'row', 'col']),
        ]

    def save(self, *args, **kwargs):
        if self.spot_type:
            self.label = f"{self.spot_type.prefix}{self.number}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.label} ({self.row}, {self.col}) - {self.layout.name}"


class StaffLocation(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Mapping between Staff (User) and Locations.
    """
    staff = models.ForeignKey(User, on_delete=models.CASCADE, related_name='staff_locations')
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='location_staff')

    class Meta:
        unique_together = ['staff', 'location']


class StaffAvailability(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Defines bookable windows or blackouts for 1-on-1 appointments and eligibility.
    """
    staff = models.ForeignKey(User, on_delete=models.CASCADE, related_name='availabilities')
    weekday_or_date = models.CharField(max_length=50, help_text="Day of week (e.g. 'monday') or YYYY-MM-DD string")
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_blackout = models.BooleanField(default=False)

    def __str__(self):
        type_str = "Blackout" if self.is_blackout else "Available"
        return f"{self.staff.email} - {self.weekday_or_date} ({self.start_time}-{self.end_time}) [{type_str}]"


class ClassTemplate(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Reusable definition of a class (does not appear on calendar directly).
    """
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='class_templates')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    duration_min = models.PositiveIntegerField()
    default_capacity = models.PositiveIntegerField(default=10)
    intensity = models.CharField(max_length=50, blank=True)
    category = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.name} ({self.duration_min} min)"


class RecurrenceRule(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Generator settings that expand into ClassSession rows.
    """
    template = models.ForeignKey(ClassTemplate, on_delete=models.CASCADE, related_name='recurrence_rules')
    days_of_week = models.JSONField(help_text="List of weekdays, e.g. ['monday', 'wednesday']")
    start_date = models.DateField()
    end_date = models.DateField()
    start_time = models.TimeField()
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True, related_name='recurrence_rules')
    staff = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='recurrence_rules', limit_choices_to={'role__in': ['trainer', 'gym_owner', 'gym_manager']})

    def __str__(self):
        return f"Rule for {self.template.name} ({self.start_date} to {self.end_date})"


class ClassSession(UUIDMixin, TimestampMixin, TenantMixin):
    """
    An individual bookable class instance on the calendar.
    """
    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
    ]
    template = models.ForeignKey(ClassTemplate, on_delete=models.CASCADE, related_name='sessions')
    recurrence_rule = models.ForeignKey(RecurrenceRule, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions')
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions')
    layout = models.ForeignKey('RoomLayout', on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions')
    layout_version = models.PositiveIntegerField(null=True, blank=True, help_text="Pinned layout version snapshot")
    blocked_spots = models.JSONField(default=list, blank=True, help_text="List of spot IDs blocked for this occurrence")
    staff = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions', limit_choices_to={'role__in': ['trainer', 'gym_owner', 'gym_manager']})
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField()
    capacity = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')

    class Meta:
        ordering = ['start_at']

    def __str__(self):
        return f"{self.template.name} on {self.start_at} ({self.status})"

    def save(self, *args, **kwargs):
        if self.layout and not self.layout_version:
            self.layout_version = self.layout.version
        if self.status == 'scheduled':
            now = timezone.now()
            if (self.end_at and self.end_at <= now) or (not self.end_at and self.start_at and self.start_at <= now):
                self.status = 'completed'
        super().save(*args, **kwargs)

    @classmethod
    def auto_complete_past_sessions(cls, tenant=None):
        """
        Bulk updates all past scheduled sessions whose end time has passed to 'completed'.
        """
        now = timezone.now()
        qs = cls.all_objects.filter(
            models.Q(status='scheduled') & (
                models.Q(end_at__lte=now) |
                models.Q(end_at__isnull=True, start_at__lte=now)
            )
        )
        if tenant:
            qs = qs.filter(tenant=tenant)
        return qs.update(status='completed')

    def refresh_status(self, save=True):
        """
        Checks if the session's scheduled end time has passed and transitions status to 'completed'.
        """
        now = timezone.now()
        is_past = (self.end_at and self.end_at <= now) or (not self.end_at and self.start_at and self.start_at <= now)
        if self.status == 'scheduled' and is_past:
            self.status = 'completed'
            if save and self.pk:
                ClassSession.all_objects.filter(id=self.id, status='scheduled').update(status='completed')
        return self.status

    @property
    def is_full(self):
        return self.bookings.filter(status__in=['booked', 'checked_in', 'attended']).count() >= self.capacity

    @property
    def name(self):
        # Legacy compat
        return self.template.name if self.template_id else ""

    @property
    def start_time(self):
        # Legacy compat
        return self.start_at

    @property
    def end_time(self):
        # Legacy compat
        return self.end_at


class PackageType(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Purchasable credit package (e.g. '10-class pack').
    """
    BILLING_CYCLE_CHOICES = [
        ('weekly', 'Weekly'),
        ('monthly', 'Monthly'),
        ('yearly', 'Yearly'),
    ]

    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='package_types')
    name = models.CharField(max_length=100)
    credit_count = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    validity_days = models.PositiveIntegerField(help_text="Validity period in days after purchase")
    
    stripe_product_id = models.CharField(
        max_length=255, blank=True, null=True,
        help_text="Stripe Product ID on the Platform account"
    )
    stripe_price_id = models.CharField(
        max_length=255, blank=True, null=True,
        help_text="Stripe Price ID on the Platform account"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this package is active and purchaseable"
    )
    billing_cycle = models.CharField(
        max_length=20,
        choices=BILLING_CYCLE_CHOICES,
        default='monthly',
        help_text="Billing frequency for the package subscription"
    )

    def __str__(self):
        return f"{self.name} - {self.credit_count} credits"


class PackageGrantSource(models.TextChoices):
    PURCHASE = 'PURCHASE', _('Direct Purchase / Stripe Subscription')
    MANUAL_COMPLIMENTARY = 'MANUAL_COMPLIMENTARY', _('Manual Staff/Admin Complimentary Assignment')
    REWARD_RULE = 'REWARD_RULE', _('Reward Rule Trigger')
    REWARD_REDEMPTION = 'REWARD_REDEMPTION', _('Reward Store Redemption')
    SYSTEM = 'SYSTEM', _('System / Other')


class Package(UUIDMixin, TimestampMixin, TenantMixin):
    """
    An active instance of a purchased PackageType for a client.
    """
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('past_due', 'Past Due'),
        ('canceled', 'Canceled'),
    ]

    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='packages')
    package_type = models.ForeignKey(PackageType, on_delete=models.PROTECT, related_name='purchased_packages')
    credits_remaining = models.PositiveIntegerField()
    purchased_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    
    stripe_subscription_id = models.CharField(
        max_length=255, blank=True, null=True, unique=True,
        help_text="Stripe Subscription ID (sub_...) for the package"
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='active',
        help_text="Subscription status"
    )
    cancel_at_period_end = models.BooleanField(
        default=False,
        help_text="Whether the client has requested cancellation at the end of the billing period"
    )
    price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Price of the package at the time of purchase"
    )
    grant_source = models.CharField(
        max_length=30,
        choices=PackageGrantSource.choices,
        default=PackageGrantSource.PURCHASE,
        help_text="Origin source of the package grant"
    )
    is_complimentary = models.BooleanField(
        default=False,
        help_text="Whether this package was granted as a free/complimentary package manually by gym staff/admin"
    )
    assigned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_packages',
        help_text="Staff or Admin who manually assigned this package"
    )

    def save(self, *args, **kwargs):
        if self.price is None and self.package_type_id:
            self.price = self.package_type.price
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.package_type.name} for {self.client.email} ({self.credits_remaining} left)"


    def is_valid_for_date(self, target_date):
        target_d = target_date.date() if hasattr(target_date, 'date') else target_date
        return (
            self.credits_remaining > 0 and
            self.expires_at.date() >= target_d
        )


class Booking(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Client's reservation of a slot in a ClassSession.
    """
    STATUS_CHOICES = [
        ('booked', 'Booked'),
        ('checked_in', 'Checked In'),
        ('attended', 'Attended'),
        ('cancelled', 'Cancelled'),
        ('no_show', 'No Show'),
        ('unassigned', 'Unassigned'),
        ('waitlisted', 'Waitlisted'),
    ]
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookings')
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name='bookings')
    spot = models.ForeignKey('Spot', on_delete=models.SET_NULL, null=True, blank=True, related_name='bookings')
    is_guest = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='booked')
    credit_source = models.ForeignKey(Package, on_delete=models.PROTECT, related_name='bookings', null=True, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)
     
    join_mode = models.CharField(max_length=20, default='physical')
    music_preference = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['client', 'session']
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'spot'],
                condition=models.Q(status='booked', spot__isnull=False),
                name='unique_active_spot_booking'
            )
        ]

    def __str__(self):
        spot_str = f" [Spot: {self.spot.label}]" if self.spot else ""
        return f"{self.client.email} booked {self.session.template.name}{spot_str} ({self.status})"


class Appointment(UUIDMixin, TimestampMixin, TenantMixin):
    """
    1-on-1 private appointments (e.g. private personal training).
    """
    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
        ('no_show', 'No Show'),
    ]
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='appointments')
    provider = models.ForeignKey(User, on_delete=models.CASCADE, related_name='provider_appointments', limit_choices_to={'role__in': ['trainer', 'gym_owner', 'gym_manager']})
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='appointments')
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True, related_name='appointments')
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    credit_source = models.ForeignKey(Package, on_delete=models.SET_NULL, null=True, blank=True, related_name='appointments')

    def __str__(self):
        return f"1-on-1: {self.client.email} with {self.provider.email} at {self.start_at}"

    def save(self, *args, **kwargs):
        if self.status == 'scheduled':
            now = timezone.now()
            if (self.end_at and self.end_at <= now) or (not self.end_at and self.start_at and self.start_at <= now):
                self.status = 'completed'
        super().save(*args, **kwargs)

    @classmethod
    def auto_complete_past_appointments(cls, tenant=None):
        """
        Bulk updates all past scheduled appointments whose end time has passed to 'completed'.
        """
        now = timezone.now()
        qs = cls.all_objects.filter(
            models.Q(status='scheduled') & (
                models.Q(end_at__lte=now) |
                models.Q(end_at__isnull=True, start_at__lte=now)
            )
        )
        if tenant:
            qs = qs.filter(tenant=tenant)
        return qs.update(status='completed')

    def refresh_status(self, save=True):
        """
        Checks if the appointment's scheduled end time has passed and transitions status to 'completed'.
        """
        now = timezone.now()
        is_past = (self.end_at and self.end_at <= now) or (not self.end_at and self.start_at and self.start_at <= now)
        if self.status == 'scheduled' and is_past:
            self.status = 'completed'
            if save and self.pk:
                Appointment.all_objects.filter(id=self.id, status='scheduled').update(status='completed')
        return self.status


class Waitlist(UUIDMixin, TimestampMixin, TenantMixin):
    """
    FIFO waitlist queue for full class sessions.
    """
    STATUS_CHOICES = [
        ('waiting', 'Waiting'),
        ('offered', 'Offered'),
        ('expired', 'Expired'),
        ('converted', 'Converted'),
    ]
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='waitlists')
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name='waitlists')
    position = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='waiting')
    offered_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Waitlist {self.position}: {self.client.email} for {self.session.template.name}"


class SubstituteRequest(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Staff request for class session substitute coverage.
    """
    STATUS_CHOICES = [
        ('open', 'Open'),
        ('filled', 'Filled'),
        ('expired', 'Expired'),
    ]
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name='substitute_requests')
    requested_by_staff = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requested_substitutes')
    accepted_by_staff = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='accepted_substitutes')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')

    def __str__(self):
        return f"Sub Request for {self.session} by {self.requested_by_staff.email} ({self.status})"


class Payment(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Transaction records for commerce.
    """
    TYPE_CHOICES = [
        ('package_purchase', 'Package Purchase'),
        ('drop_in', 'Drop-in'),
        ('fee', 'Fee'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    related_booking = models.ForeignKey(Booking, on_delete=models.SET_NULL, null=True, blank=True, related_name='payments')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='completed')
    provider_ref = models.CharField(max_length=255, blank=True)
    idempotency_key = models.CharField(max_length=255, unique=True, db_index=True)

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        
        # Automatically generate PlatformLedger on successful payment using LedgerService
        if self.status == 'completed' and not hasattr(self, 'ledger_entry'):
            from apps.payments.services import LedgerService
            LedgerService.record_transaction(
                tenant=self.tenant,
                amount_gross=self.amount,
                transaction_id=str(self.id),
                payment_instance=self,
                description=f"Payment for {self.type}"
            )

        # Emit Rewards Event for completed payments
        if self.status == 'completed':
            try:
                from apps.rewards.events import RewardEvent
                from apps.rewards.services import RewardEngineService
                
                RewardEngineService.handle_event(RewardEvent.create_payment_completed(
                    tenant_id=self.tenant_id,
                    user_id=self.client_id,
                    payment_id=self.id,
                    amount=self.amount,
                    payment_type=self.type
                ))
            except Exception:
                pass

    def __str__(self):
        return f"Payment {self.id} - {self.type} - ${self.amount}"


class CancellationPolicy(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Fee and cutoff configurations for booking cancellations.
    """
    SCOPE_CHOICES = [
        ('template', 'Template'),
        ('tier', 'Tier'),
        ('global', 'Global'),
    ]
    scope_type = models.CharField(max_length=20, choices=SCOPE_CHOICES, default='global')
    template = models.ForeignKey(ClassTemplate, on_delete=models.CASCADE, null=True, blank=True, related_name='cancellation_policies')
    membership_tier = models.CharField(max_length=50, null=True, blank=True, help_text="e.g. VIP, Gold")
    cutoff_hours = models.PositiveIntegerField(default=12, help_text="Cancel window cutoff in hours")
    late_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return f"Policy {self.scope_type} - {self.cutoff_hours}h cutoff"


class StaffClientAssignment(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Mapping linking trainer to client.
    """
    staff = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='assigned_client_relations',
        limit_choices_to={'role__in': ['trainer', 'gym_owner', 'gym_manager']}
    )
    client = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='assigned_staff_relations',
        limit_choices_to={'role': 'client'}
    )

    class Meta:
        unique_together = ['staff', 'client']

    def clean(self):
        if self.staff.role not in ['trainer', 'gym_owner', 'gym_manager']:
            raise ValidationError("Assigned user must be staff.")
        if self.client.role != 'client':
            raise ValidationError("Assigned target must be a client.")

class FacilityAccessLog(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Tracks when a client physically enters or leaves the gym location.
    """
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='facility_access_logs')
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='access_logs')
    checked_in_at = models.DateTimeField(auto_now_add=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.client.email} at {self.location.name} (In: {self.checked_in_at})"


class ClientBookingPreference(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Stores each Client's personal booking preferences against their authenticated account:
    - Join Mode / Remote Session (e.g. 'physical', 'remote', 'virtual')
    - Virtual Coach (e.g. 'virtual', 'in_person')
    - Music Preference (e.g. 'Chill Hop', 'Upbeat EDM', 'Rock', 'Pop')
    """
    client = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='booking_preferences'
    )
    join_mode = models.CharField(
        max_length=50,
        default='physical',
        help_text=_("Preferred attendance mode: 'physical', 'remote', etc.")
    )
    virtual_coach = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text=_("Preferred coach mode (e.g. 'virtual', 'in_person')")
    )
    music_preference = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text=_("Preferred music genre/station (e.g. 'Chill Hop', 'Upbeat EDM')")
    )

    class Meta:
        ordering = ['-updated_at']
        verbose_name = _('Client Booking Preference')
        verbose_name_plural = _('Client Booking Preferences')

    def __str__(self):
        return f"Booking Preferences for {self.client.email} ({self.join_mode})"

    def save(self, *args, **kwargs):
        if not self.tenant_id and self.client_id and hasattr(self.client, 'tenant') and self.client.tenant:
            self.tenant = self.client.tenant
        super().save(*args, **kwargs)


class Event(UUIDMixin, TimestampMixin, TenantMixin, SoftDeleteModel):
    """
    Mindbody-style Event / Workshop / Enrollment.
    Special scheduled offering (single-session workshop or multi-session bootcamp series),
    distinguished from ongoing recurring classes and personal appointments.
    Can be free or paid (requires client pass / package credits).
    """
    CATEGORY_CHOICES = [
        ('workshop', 'Workshop'),
        ('bootcamp', 'Bootcamp'),
        ('seminar', 'Seminar'),
        ('retreat', 'Retreat'),
        ('certification', 'Certification'),
        ('masterclass', 'Masterclass'),
        ('community', 'Community Event'),
    ]
    EVENT_TYPE_CHOICES = [
        ('single', 'Single Session'),
        ('series', 'Multi-Session Series'),
    ]
    ENROLLMENT_TYPE_CHOICES = [
        ('entire_series', 'Entire Series Only'),
        ('drop_in_allowed', 'Drop-in Allowed'),
    ]
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='workshop')
    image = models.ImageField(upload_to='events/', blank=True, null=True)

    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='events')
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True, related_name='events')
    primary_instructor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='led_events',
        limit_choices_to={'role__in': ['trainer', 'gym_owner', 'gym_manager']}
    )
    assistant_instructors = models.ManyToManyField(
        User, blank=True, related_name='assisted_events',
        limit_choices_to={'role__in': ['trainer', 'gym_owner', 'gym_manager']}
    )

    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES, default='single')
    enrollment_type = models.CharField(max_length=20, choices=ENROLLMENT_TYPE_CHOICES, default='entire_series')

    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField()

    registration_opens_at = models.DateTimeField(null=True, blank=True)
    registration_closes_at = models.DateTimeField(null=True, blank=True)

    capacity = models.PositiveIntegerField(help_text="Maximum number of attendees")
    waitlist_capacity = models.PositiveIntegerField(default=0, help_text="Maximum waitlist size")

    is_free = models.BooleanField(default=False, help_text="True if free event; False if requires active client pass")
    credits_required = models.PositiveIntegerField(default=1, help_text="Number of pass credits required if paid")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='published')
    cancellation_cutoff_hours = models.PositiveIntegerField(default=24, help_text="Hours before start eligible for pass credit refund")
    terms_and_conditions = models.TextField(blank=True)

    objects = TenantSoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        ordering = ['start_at']
        indexes = [
            models.Index(fields=['location', 'status', 'start_at']),
        ]

    def __str__(self):
        return f"{self.title} ({self.get_category_display()}) - {self.location.name}"

    @property
    def is_registration_open(self):
        now = timezone.now()
        if self.status not in ['published', 'in_progress']:
            return False
        if self.registration_opens_at and now < self.registration_opens_at:
            return False
        if self.registration_closes_at and now > self.registration_closes_at:
            return False
        if not self.registration_closes_at and now > self.start_at:
            return False
        return True

    @property
    def enrolled_count(self):
        return self.enrollments.filter(status__in=['registered', 'attended']).count()

    @property
    def waitlist_count(self):
        return self.enrollments.filter(status='waitlisted').count()

    @property
    def spots_remaining(self):
        return max(0, self.capacity - self.enrolled_count)

    @property
    def is_full(self):
        return self.enrolled_count >= self.capacity

    @property
    def is_waitlist_full(self):
        if self.waitlist_capacity <= 0:
            return True
        return self.waitlist_count >= self.waitlist_capacity


class EventSession(UUIDMixin, TimestampMixin, TenantMixin):
    """
    An individual date/time session belonging to a multi-session Event series.
    """
    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='sessions')
    session_number = models.PositiveIntegerField(default=1)
    title = models.CharField(max_length=150, blank=True)
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True, related_name='event_sessions')
    instructor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='event_sessions',
        limit_choices_to={'role__in': ['trainer', 'gym_owner', 'gym_manager']}
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')

    class Meta:
        ordering = ['session_number', 'start_at']

    def __str__(self):
        return f"{self.event.title} - Session {self.session_number}: {self.title or self.start_at}"


class EventEnrollment(UUIDMixin, TimestampMixin, TenantMixin):
    """
    An individual Client's registration for an Event/Workshop.
    Tracks pass credit deductions, waitlist status, check-in, and multi-session attendance.
    """
    STATUS_CHOICES = [
        ('registered', 'Registered'),
        ('waitlisted', 'Waitlisted'),
        ('attended', 'Attended'),
        ('cancelled', 'Cancelled'),
        ('no_show', 'No Show'),
    ]
    PRICING_TYPE_CHOICES = [
        ('free', 'Free Event'),
        ('pass', 'Client Pass Credit'),
        ('complimentary', 'Staff Complimentary'),
    ]

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='enrollments')
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='event_enrollments')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='registered')
    pricing_type = models.CharField(max_length=20, choices=PRICING_TYPE_CHOICES, default='pass')

    credit_source = models.ForeignKey(
        'Package', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='event_enrollments',
        help_text="The client pass instance from which credit was deducted (if paid)"
    )
    credits_deducted = models.PositiveIntegerField(default=0)

    attended_sessions = models.ManyToManyField(
        EventSession, blank=True, related_name='attended_enrollments',
        help_text="Track session-by-session attendance for multi-day workshops"
    )

    checked_in_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)
    notes = models.TextField(blank=True, help_text="Special instructions, dietary restrictions, emergency contact")

    class Meta:
        ordering = ['-created_at']
        unique_together = ['event', 'client']

    def __str__(self):
        return f"{self.client.email} enrolled in {self.event.title} ({self.status})"


