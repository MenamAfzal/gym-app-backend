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

class SessionType(models.TextChoices):
    PHYSICAL = 'physical', _('Physical')
    VIRTUAL = 'virtual', _('Virtual')

class StaffClientAssignment(UUIDMixin, TimestampMixin, TenantMixin):
    """
    M2M link between Staff (Trainer) and Client.
    Defines which staff manages which client.
    """
    staff = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='assigned_client_relations',
        limit_choices_to={'role': 'trainer'}
    )
    client = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='assigned_staff_relations',
        limit_choices_to={'role': 'client'}
    )

    class Meta:
        unique_together = ['staff', 'client']
        indexes = [
            models.Index(fields=['staff', 'client']),
        ]

    def clean(self):
        if self.staff.role != 'trainer':
            raise ValidationError("Assigned user must be a trainer.")
        if self.client.role != 'client':
            raise ValidationError("Assigned target must be a client.")

class PricingOption(UUIDMixin, TimestampMixin, TenantMixin):
    """
    Defines the product/plan a client buys.
    """
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    session_credits = models.PositiveIntegerField(help_text="Number of sessions this option grants")
    
    # Expiry Logic
    duration_days = models.PositiveIntegerField(
        null=True, blank=True, 
        help_text="Valid for X days after purchase (e.g. 30)"
    )
    fixed_start_date = models.DateField(
        null=True, blank=True, 
        help_text="Hard start date (e.g. Semester start). Overrides duration."
    )
    fixed_expiry_date = models.DateField(
        null=True, blank=True, 
        help_text="Hard expiry date. Overrides duration."
    )

    def __str__(self):
        return f"{self.name} ({self.session_credits} credits)"

    def clean(self):
        if not self.duration_days and not self.fixed_expiry_date:
            raise ValidationError("You must specify either a duration or a fixed expiry date.")
        if self.fixed_start_date and self.fixed_expiry_date:
            if self.fixed_start_date >= self.fixed_expiry_date:
                raise ValidationError("Start date must be before expiry date.")

class ClientPass(UUIDMixin, TimestampMixin, TenantMixin):
    """
    The actual 'wallet' item assigned to a client.
    """
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='passes')
    pricing_option = models.ForeignKey(PricingOption, on_delete=models.PROTECT)
    credits_remaining = models.PositiveIntegerField(default=0)
    
    # Dates are calculated upon creation based on the PricingOption
    start_date = models.DateField()
    expiry_date = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        indexes = [models.Index(fields=['client', 'is_active'])]

    def save(self, *args, **kwargs):
        # Auto-calculate dates on creation if not set
        if not self.pk: 
            today = timezone.now().date()
            
            # Logic: Fixed dates take precedence
            if self.pricing_option.fixed_start_date:
                self.start_date = self.pricing_option.fixed_start_date
            else:
                self.start_date = today

            if self.pricing_option.fixed_expiry_date:
                self.expiry_date = self.pricing_option.fixed_expiry_date
            elif self.pricing_option.duration_days:
                self.expiry_date = self.start_date + timedelta(days=self.pricing_option.duration_days)
            else:
                # Fallback (should be caught by model validation)
                self.expiry_date = today + timedelta(days=365) 

            # Initialize credits
            if self.credits_remaining == 0: 
                self.credits_remaining = self.pricing_option.session_credits

        super().save(*args, **kwargs)

    def is_valid_for_session(self, session_date):
        """Helper to check if pass covers a specific date"""
        return (
            self.is_active and 
            self.credits_remaining > 0 and 
            self.start_date <= session_date.date() <= self.expiry_date
        )
class Session(UUIDMixin, TimestampMixin, TenantMixin):
    """
    A specific class/slot created by Admin.
    """
    title = models.CharField(max_length=255)
    staff = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        related_name='sessions_leading',
        limit_choices_to={'role': 'trainer'}
    )
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField()
    capacity = models.PositiveIntegerField(default=10)
    
    session_type = models.CharField(max_length=20, choices=SessionType.choices, default=SessionType.PHYSICAL)
    meeting_url = models.URLField(blank=True, null=True, help_text="Zoom/Meet link if virtual")
    
    # Prerequisite: Does this session require a specific pricing option?
    # For MVP, we assume any active pass works, but we can link specific PricingOptions here later.

    class Meta:
        ordering = ['start_time']
        indexes = [
            models.Index(fields=['start_time', 'staff']),
        ]

    def __str__(self):
        return f"{self.title} ({self.start_time})"

    @property
    def is_full(self):
        return self.bookings.filter(status='booked').count() >= self.capacity

class Booking(UUIDMixin, TimestampMixin, TenantMixin):
    STATUS_CHOICES = [
        ('booked', 'Booked'),
        ('cancelled', 'Cancelled'),
        ('completed', 'Completed'),
    ]
    
    session = models.ForeignKey('Session', on_delete=models.CASCADE, related_name='bookings')
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bookings')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='booked')
    
    # We track WHICH pass was used, so we know where to refund
    used_pass = models.ForeignKey(
        ClientPass, 
        on_delete=models.PROTECT, 
        related_name='bookings',
        null=True, blank=True
    )
    
    join_mode = models.CharField(max_length=20, default='physical')
    music_preference = models.CharField(max_length=100, blank=True)

    class Meta:
        unique_together = ['session', 'client']

    def save(self, *args, **kwargs):
        # Logic moved to Views/Service layer for better transactional control
        # but basic validation remains here.
        super().save(*args, **kwargs)
