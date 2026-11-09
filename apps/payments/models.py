import uuid
from django.db import models
from django.utils import timezone
from django.core.validators import MinValueValidator, MaxValueValidator
from core_models.base_models import BaseModel, TenantAwareModel


class PlatformSettings(BaseModel):
    platform_fee_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=10.00,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Default platform cut percentage (e.g., 10.00 for 10%)",
    )

    class Meta:
        verbose_name_plural = "Platform Settings"

    def __str__(self):
        return f"Platform Settings ({self.platform_fee_percentage}%)"

    @classmethod
    def get_settings(cls):
        settings = cls.objects.first()
        if not settings:
            settings = cls.objects.create()
        return settings


class BillingFeature(BaseModel):
    name = models.CharField(
        max_length=150,
        help_text="Human-readable feature name shown to gym owners",
    )
    code = models.SlugField(
        max_length=100,
        unique=True,
        help_text="Internal feature identifier (e.g. 'advanced_analytics')",
    )
    description = models.TextField(
        blank=True,
        help_text="Short description shown on the billing/upgrade page",
    )
    stripe_product_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Stripe Product ID for this feature",
    )
    stripe_price_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Stripe Price ID (price_...) for this feature's recurring charge",
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Recurring price of the feature subscription",
    )
    billing_cycle = models.CharField(
        max_length=20,
        choices=[('weekly', 'Weekly'), ('monthly', 'Monthly')],
        default='monthly',
        help_text="Billing frequency for this feature",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive features are hidden from the checkout UI",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Billing Feature"
        verbose_name_plural = "Billing Features"

    def __str__(self):
        return f"{self.name} ({self.code})"


class BillingPlan(BaseModel):
    class PlanSlug(models.TextChoices):
        FREE = "free", "Free"
        BASIC = "basic", "Basic"
        PREMIUM = "premium", "Premium"
        CUSTOM = "custom", "Custom"

    name = models.CharField(max_length=100, help_text="Display name")
    slug = models.CharField(
        max_length=20,
        choices=PlanSlug.choices,
        unique=True,
        help_text="Internal plan identifier",
    )
    allowed_feature_count = models.IntegerField(
        null=True,
        blank=True,
        help_text=(
            "Required feature count for this plan: "
            "0 = none, 3 = exactly 3, None = all, -1 = any (custom)"
        ),
    )
    is_public = models.BooleanField(
        default=True,
        help_text="Whether gym owners can self-select this plan",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Billing Plan"
        verbose_name_plural = "Billing Plans"

    def __str__(self):
        return f"{self.name} (slug={self.slug})"


class TenantBillingSubscription(TenantAwareModel):
    class StatusChoices(models.TextChoices):
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past Due"
        CANCELED = "canceled", "Canceled"
        INCOMPLETE = "incomplete", "Incomplete"

    billing_plan = models.ForeignKey(
        BillingPlan,
        on_delete=models.PROTECT,
        related_name="tenant_subscriptions",
        help_text="The plan tier this subscription belongs to",
    )
    active_features = models.ManyToManyField(
        BillingFeature,
        blank=True,
        related_name="subscriptions",
        help_text="The specific premium features unlocked for this tenant",
    )
    stripe_subscription_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        unique=True,
        db_index=True,
        help_text="Stripe Subscription ID (sub_...) created after payment",
    )
    stripe_checkout_session_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        help_text="Stripe Checkout Session ID (cs_...) used to initiate payment",
    )
    status = models.CharField(
        max_length=20,
        choices=StatusChoices.choices,
        default=StatusChoices.INCOMPLETE,
        help_text="Current billing status",
    )
    current_period_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the current billing period ends (populated by webhook)",
    )
    cancel_at_period_end = models.BooleanField(
        default=False,
        help_text="Whether the Gym Admin has requested plan cancellation at the end of the billing period",
    )
    platform_features = models.JSONField(
        default=dict,
        blank=True
    )

    @property
    def is_entitled(self) -> bool:
        """
        Evaluates whether this subscription currently entitles the tenant to its features.
        The subscription is entitled if its status is ACTIVE and the current billing cycle
        has not expired. If cancel_at_period_end is True, access remains active until current_period_end.
        """
        if self.status != self.StatusChoices.ACTIVE:
            return False
        if self.current_period_end:
            cpe = self.current_period_end
            if timezone.is_naive(cpe):
                cpe = timezone.make_aware(cpe, timezone.utc)
            if cpe < timezone.now():
                return False
        return True

    class Meta:
        verbose_name = "Tenant Billing Subscription"
        verbose_name_plural = "Tenant Billing Subscriptions"

    def __str__(self):
        plan_name = self.billing_plan.name if self.billing_plan_id else "No Plan"
        return f"{self.tenant} – {plan_name} ({self.status})"


class TenantSubscription(TenantAwareModel):
    class PlanChoices(models.TextChoices):
        PLAN_A = "plan_a", "Plan A (Basic)"
        PLAN_B = "plan_b", "Plan B (Pro)"
        PLAN_C = "plan_c", "Plan C (Enterprise)"

    class StatusChoices(models.TextChoices):
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past Due"
        CANCELED = "canceled", "Canceled"
        INCOMPLETE = "incomplete", "Incomplete"

    plan_name = models.CharField(
        max_length=20, choices=PlanChoices.choices, default=PlanChoices.PLAN_A
    )
    stripe_subscription_id = models.CharField(
        max_length=100, blank=True, null=True, unique=True
    )
    status = models.CharField(
        max_length=20,
        choices=StatusChoices.choices,
        default=StatusChoices.INCOMPLETE,
    )
    current_period_end = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Tenant Subscription (Legacy)"
        verbose_name_plural = "Tenant Subscriptions (Legacy)"

    def __str__(self):
        return f"{self.tenant} - {self.plan_name} ({self.status})"


class FeatureToggle(TenantAwareModel):
    feature_name = models.CharField(max_length=100)
    is_enabled = models.BooleanField(default=True)
    stripe_price_id = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        unique_together = ("tenant", "feature_name")
        verbose_name = "Feature Toggle (Legacy)"
        verbose_name_plural = "Feature Toggles (Legacy)"

    def __str__(self):
        return f"{self.tenant} - {self.feature_name} (Enabled: {self.is_enabled})"


class PlatformLedger(TenantAwareModel):
    class TransactionType(models.TextChoices):
        CHARGE = "charge", "Charge"
        REFUND = "refund", "Refund"
        SUBSCRIPTION = "sub", "Tenant Subscription"

    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending Payout"
        PAID = "paid", "Paid Out"
        FAILED = "failed", "Payout Failed"

    transaction_id = models.CharField(
        max_length=100, unique=True, help_text="Stripe Charge ID or UUID"
    )
    amount_gross = models.DecimalField(
        max_digits=10, decimal_places=2, help_text="Total amount charged to client"
    )
    platform_fee = models.DecimalField(
        max_digits=10, decimal_places=2, help_text="Platform's cut"
    )
    amount_net = models.DecimalField(
        max_digits=10, decimal_places=2, help_text="Amount owed to tenant"
    )
    currency = models.CharField(max_length=3, default="usd")
    type = models.CharField(
        max_length=10,
        choices=TransactionType.choices,
        default=TransactionType.CHARGE,
    )
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=StatusChoices.choices, default=StatusChoices.PENDING
    )
    payout = models.ForeignKey(
        "TenantPayout",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )

    def __str__(self):
        return f"{self.type.capitalize()} - {self.amount_gross} {self.currency} ({self.status})"


class TenantPayout(TenantAwareModel):
    class StatusChoices(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Total amount transferred to tenant",
    )
    currency = models.CharField(max_length=3, default="usd")
    status = models.CharField(
        max_length=20, choices=StatusChoices.choices, default=StatusChoices.PENDING
    )
    stripe_payout_id = models.CharField(
        max_length=100, blank=True, null=True, unique=True
    )

    def __str__(self):
        return f"Payout - {self.amount} {self.currency} ({self.status})"


class TenantCheckoutConfig(BaseModel):
    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='checkout_config'
    )
    is_enabled = models.BooleanField(default=False)
    allowed_origins = models.JSONField(default=list, blank=True)
    enabled_package_ids = models.JSONField(default=list, blank=True)
    theme_primary_color = models.CharField(max_length=20, default='#000000')
    widget_layout = models.CharField(max_length=20, default='grid')
    widget_button_text = models.CharField(max_length=50, default='Select Plan')
    show_location_filter = models.BooleanField(default=True)
    require_phone_number = models.BooleanField(default=False)
    terms_url = models.URLField(null=True, blank=True)
    success_redirect_url = models.URLField(null=True, blank=True)
    cancel_redirect_url = models.URLField(null=True, blank=True)
    meta_pixel_id = models.CharField(max_length=64, null=True, blank=True)
    meta_capi_access_token = models.TextField(null=True, blank=True)
    meta_test_event_code = models.CharField(max_length=64, null=True, blank=True)

    def __str__(self):
        return f"CheckoutConfig - {self.tenant.name} (enabled={self.is_enabled})"


class CheckoutVerificationChallenge(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='checkout_challenges'
    )
    package_type = models.ForeignKey(
        'scheduling.PackageType',
        on_delete=models.CASCADE,
        related_name='checkout_challenges'
    )
    email = models.EmailField(db_index=True)
    otp_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
    is_verified = models.BooleanField(default=False)
    session_created = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'package_type', 'email']),
            models.Index(fields=['expires_at', 'is_verified']),
        ]

    def __str__(self):
        return f"Challenge {self.id} ({self.email})"


class ExternalCheckoutSession(TenantAwareModel):
    class StatusChoices(models.TextChoices):
        PENDING_VERIFICATION = 'PENDING_VERIFICATION', 'Pending Verification'
        READY_FOR_PAYMENT = 'READY_FOR_PAYMENT', 'Ready For Payment'
        PAYMENT_PENDING = 'PAYMENT_PENDING', 'Payment Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        PAYMENT_FAILED = 'PAYMENT_FAILED', 'Payment Failed'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELLED = 'CANCELLED', 'Cancelled'
        PRICE_MISMATCH = 'PRICE_MISMATCH', 'Price Mismatch'

    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    challenge = models.ForeignKey(
        CheckoutVerificationChallenge,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checkout_sessions'
    )
    package_type = models.ForeignKey(
        'scheduling.PackageType',
        on_delete=models.PROTECT,
        related_name='external_checkout_sessions'
    )
    email = models.EmailField(db_index=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=25, blank=True)
    is_verified = models.BooleanField(default=False)
    otp_hash = models.CharField(max_length=64, blank=True, null=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)
    otp_expires_at = models.DateTimeField(null=True, blank=True)
    otp_last_sent_at = models.DateTimeField(null=True, blank=True)
    price_snapshot = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='usd')
    billing_cycle_snapshot = models.CharField(max_length=20)
    credit_count_snapshot = models.PositiveIntegerField()
    validity_days_snapshot = models.PositiveIntegerField()
    status = models.CharField(
        max_length=25,
        choices=StatusChoices.choices,
        default=StatusChoices.PENDING_VERIFICATION
    )
    stripe_checkout_session_id = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True
    )
    stripe_subscription_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True
    )
    customer_user = models.ForeignKey(
        'users.User',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='checkout_sessions'
    )
    created_package = models.OneToOneField(
        'scheduling.Package',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='checkout_session'
    )
    payment_record = models.OneToOneField(
        'scheduling.Payment',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='checkout_session'
    )
    is_new_customer = models.BooleanField(default=False)
    initiate_event_id = models.UUIDField(default=uuid.uuid4)
    purchase_event_id = models.UUIDField(default=uuid.uuid4)
    fbp = models.CharField(max_length=255, null=True, blank=True)
    fbc = models.CharField(max_length=255, null=True, blank=True)
    client_ip_address = models.GenericIPAddressField(null=True, blank=True)
    client_user_agent = models.TextField(null=True, blank=True)
    source_url = models.URLField(max_length=500, null=True, blank=True)
    tracking_purged_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'status', 'expires_at']),
            models.Index(fields=['tenant', 'email', 'status']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(price_snapshot__gt=0),
                name='checkout_session_positive_price'
            )
        ]

    def __str__(self):
        return f"CheckoutSession {self.id} ({self.email} - {self.status})"

    @property
    def payment_status(self):
        if self.status == self.StatusChoices.COMPLETED:
            return 'successful'
        if self.status == self.StatusChoices.PAYMENT_PENDING:
            return 'processing'
        if self.status in [self.StatusChoices.PENDING_VERIFICATION, self.StatusChoices.READY_FOR_PAYMENT]:
            return 'pending'
        if self.status in [self.StatusChoices.PAYMENT_FAILED, self.StatusChoices.PRICE_MISMATCH]:
            return 'failed'
        if self.status == self.StatusChoices.EXPIRED:
            return 'expired'
        if self.status == self.StatusChoices.CANCELLED:
            return 'cancelled'
        return 'pending'

    @property
    def can_proceed(self):
        return (
            self.is_verified
            and self.status in [self.StatusChoices.READY_FOR_PAYMENT, self.StatusChoices.PAYMENT_PENDING]
            and self.expires_at > timezone.now()
        )



class StripeProcessedEvent(BaseModel):
    event_id = models.CharField(max_length=255, unique=True, db_index=True)
    event_type = models.CharField(max_length=100)
    processed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.event_type} ({self.event_id})"


