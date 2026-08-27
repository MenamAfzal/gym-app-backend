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


class Room(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Rooms within a specific Location. Used for conflict checking.
    """
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='rooms')
    name = models.CharField(max_length=100)
    capacity = models.PositiveIntegerField()
    equipment_tags = models.JSONField(default=list, blank=True, help_text="List of equipment tags")

    def __str__(self):
        return f"{self.name} - {self.location.name}"


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
    staff = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions', limit_choices_to={'role__in': ['trainer', 'gym_owner', 'gym_manager']})
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField()
    capacity = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')

    class Meta:
        ordering = ['start_at']

    def __str__(self):
        return f"{self.template.name} on {self.start_at} ({self.status})"

    @property
    def is_full(self):
        return self.bookings.filter(status='booked').count() >= self.capacity

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
        ('cancelled', 'Cancelled'),
        ('no_show', 'No Show'),
        ('attended', 'Attended'),
    ]
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookings')
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name='bookings')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='booked')
    credit_source = models.ForeignKey(Package, on_delete=models.PROTECT, related_name='bookings', null=True, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)
     
    join_mode = models.CharField(max_length=20, default='physical')
    music_preference = models.CharField(max_length=100, blank=True)

    class Meta:
        unique_together = ['client', 'session']

    def __str__(self):
        return f"{self.client.email} booked {self.session.template.name} ({self.status})"


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
    cutoff_hours = models.PositiveIntegerField(help_text="Cancel window cutoff in hours")
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

