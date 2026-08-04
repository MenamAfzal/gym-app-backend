from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from core_models.base_models import BaseModel, TenantAwareModel

class PlatformSettings(BaseModel):
    """
    Global settings for the platform admin.
    Usually only one instance of this model should exist.
    """
    platform_fee_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=10.00,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Default platform cut percentage (e.g., 10.00 for 10%)"
    )

    class Meta:
        verbose_name_plural = "Platform Settings"

    def __str__(self):
        return f"Platform Settings ({self.platform_fee_percentage}%)"

    @classmethod
    def get_settings(cls):
        """Helper to get the singleton instance or create default."""
        settings, created = cls.objects.get_or_create(id=1)
        return settings


class TenantSubscription(TenantAwareModel):
    """
    Tracks the active subscription plan for a tenant via Stripe.
    """
    class PlanChoices(models.TextChoices):
        PLAN_A = 'plan_a', 'Plan A (Basic)'
        PLAN_B = 'plan_b', 'Plan B (Pro)'
        PLAN_C = 'plan_c', 'Plan C (Enterprise)'

    class StatusChoices(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAST_DUE = 'past_due', 'Past Due'
        CANCELED = 'canceled', 'Canceled'
        INCOMPLETE = 'incomplete', 'Incomplete'

    plan_name = models.CharField(max_length=20, choices=PlanChoices.choices, default=PlanChoices.PLAN_A)
    stripe_subscription_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.INCOMPLETE)
    current_period_end = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.tenant} - {self.plan_name} ({self.status})"


class FeatureToggle(TenantAwareModel):
    """
    Tracks a-la-carte features enabled for a tenant.
    """
    feature_name = models.CharField(max_length=100)
    is_enabled = models.BooleanField(default=True)
    stripe_price_id = models.CharField(max_length=100, blank=True, null=True)
    
    class Meta:
        unique_together = ('tenant', 'feature_name')

    def __str__(self):
        return f"{self.tenant} - {self.feature_name} (Enabled: {self.is_enabled})"


class PlatformLedger(TenantAwareModel):
    """
    Central ledger tracking transactions (e.g., client bookings) and calculating the platform cut.
    """
    class TransactionType(models.TextChoices):
        CHARGE = 'charge', 'Charge'
        REFUND = 'refund', 'Refund'

    class StatusChoices(models.TextChoices):
        PENDING = 'pending', 'Pending Payout'
        PAID = 'paid', 'Paid Out'
        FAILED = 'failed', 'Payout Failed'

    transaction_id = models.CharField(max_length=100, unique=True, help_text="Stripe Charge ID or UUID")
    amount_gross = models.DecimalField(max_digits=10, decimal_places=2, help_text="Total amount charged to client")
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2, help_text="Platform's cut")
    amount_net = models.DecimalField(max_digits=10, decimal_places=2, help_text="Amount owed to tenant")
    currency = models.CharField(max_length=3, default='usd')
    
    type = models.CharField(max_length=10, choices=TransactionType.choices, default=TransactionType.CHARGE)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.PENDING)
    
    # Optional link to payout
    payout = models.ForeignKey('TenantPayout', on_delete=models.SET_NULL, null=True, blank=True, related_name='ledger_entries')

    def __str__(self):
        return f"{self.type.capitalize()} - {self.amount_gross} {self.currency} ({self.status})"


class TenantPayout(TenantAwareModel):
    """
    Aggregated payout record for a tenant.
    """
    class StatusChoices(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'

    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Total amount transferred to tenant")
    currency = models.CharField(max_length=3, default='usd')
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.PENDING)
    stripe_payout_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    
    def __str__(self):
        return f"Payout - {self.amount} {self.currency} ({self.status})"
