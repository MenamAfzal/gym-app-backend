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
        settings, _ = cls.objects.get_or_create(id=1)
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

    class Meta:
        verbose_name = "Tenant Billing Subscription"
        verbose_name_plural = "Tenant Billing Subscriptions"

    def __str__(self):
        plan_name = self.billing_plan.name if self.billing_plan_id else "No Plan"
        return f"{self.tenant} – {plan_name} ({self.status})"


class GymFeatureEntitlement(TenantAwareModel):
    """
    Decoupled gym-level feature entitlement.

    Ensures that when a Platform Admin deactivates a global feature (is_active=False),
    it only prevents new purchases, but does NOT revoke access for existing gyms
    whose subscription or billing period is still active.
    """
    subscription = models.ForeignKey(
        TenantBillingSubscription,
        on_delete=models.CASCADE,
        related_name="feature_entitlements",
        help_text="The tenant billing subscription granting this entitlement",
        null=True,
        blank=True,
    )
    feature = models.ForeignKey(
        BillingFeature,
        on_delete=models.CASCADE,
        related_name="gym_entitlements",
        help_text="The feature being entitled to the gym",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Gym-level active status of this feature entitlement",
    )
    current_period_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this gym's feature access expires",
    )

    class Meta:
        unique_together = ("tenant", "feature")
        verbose_name = "Gym Feature Entitlement"
        verbose_name_plural = "Gym Feature Entitlements"

    def __str__(self):
        return f"{self.tenant} - {self.feature.name} (Active: {self.is_active})"

    def is_entitled(self) -> bool:
        """
        Check if gym has active access to this feature during billing cycle.
        Decoupled from global BillingFeature.is_active.
        """
        if not self.is_active:
            return False

        now = timezone.now()
        if self.subscription:
            if self.subscription.status != TenantBillingSubscription.StatusChoices.ACTIVE:
                return False
            if self.subscription.current_period_end and self.subscription.current_period_end < now:
                return False
        elif self.current_period_end and self.current_period_end < now:
            return False

        return True

    @classmethod
    def sync_for_subscription(cls, subscription):
        """
        Sync gym-level entitlements when a subscription is created or updated.
        """
        tenant = subscription.tenant
        features = subscription.active_features.all()

        for feat in features:
            cls.all_objects.update_or_create(
                tenant=tenant,
                feature=feat,
                defaults={
                    "subscription": subscription,
                    "is_active": subscription.status == TenantBillingSubscription.StatusChoices.ACTIVE,
                    "current_period_end": subscription.current_period_end,
                }
            )

    @classmethod
    def is_gym_entitled(cls, tenant, feature_code_or_obj) -> bool:
        """
        Determines whether a gym is actively entitled to a feature based on its individual
        entitlement status, without requiring global BillingFeature.is_active to be True.
        """
        if not tenant:
            return False

        now = timezone.now()

        if isinstance(feature_code_or_obj, BillingFeature):
            feat_filter = models.Q(feature=feature_code_or_obj)
        elif isinstance(feature_code_or_obj, str):
            try:
                import uuid
                u = uuid.UUID(feature_code_or_obj)
                feat_filter = models.Q(feature_id=u) | models.Q(feature__code=feature_code_or_obj)
            except (ValueError, AttributeError):
                feat_filter = models.Q(feature__code=feature_code_or_obj)
        else:
            feat_filter = models.Q(feature=feature_code_or_obj)

        entitlement = cls.all_objects.filter(
            models.Q(tenant=tenant) & feat_filter
        ).select_related('subscription', 'feature').first()

        if entitlement:
            return entitlement.is_entitled()

        # Fallback to checking active TenantBillingSubscriptions (self-healing for pre-existing records)
        active_subs = TenantBillingSubscription.all_objects.filter(
            tenant=tenant,
            status=TenantBillingSubscription.StatusChoices.ACTIVE
        ).prefetch_related('active_features')

        for active_sub in active_subs:
            if active_sub.current_period_end and active_sub.current_period_end < now:
                continue
            for feat in active_sub.active_features.all():
                matched = False
                if isinstance(feature_code_or_obj, BillingFeature) and feat.id == feature_code_or_obj.id:
                    matched = True
                elif isinstance(feature_code_or_obj, str) and (feat.code == feature_code_or_obj or str(feat.id) == feature_code_or_obj):
                    matched = True

                if matched:
                    try:
                        cls.all_objects.update_or_create(
                            tenant=tenant,
                            feature=feat,
                            defaults={
                                'subscription': active_sub,
                                'is_active': True,
                                'current_period_end': active_sub.current_period_end,
                            }
                        )
                    except Exception:
                        pass
                    return True

        return False


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

