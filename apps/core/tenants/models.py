"""
Tenant and Subscription Models

This module contains all models related to multi-tenancy, subscription plans,
features, and entitlements. These models form the backbone of the multi-tenant
SaaS architecture.
"""
from django.db import models
from django.core.validators import MinValueValidator
from core_models.mixins.uuid_mixin import UUIDMixin
from core_models.mixins.timestamps import TimestampMixin


class Tenant(UUIDMixin, TimestampMixin):
    """
    Represents a gym organization (tenant) in the multi-tenant system.
    Each tenant is isolated and has its own subdomain.
    """
    name = models.CharField(max_length=255, help_text="Organization name")
    subdomain = models.SlugField(
        max_length=100, 
        unique=True, 
        help_text="Unique subdomain (e.g., 'gym1' in gym1.example.com)"
    )
    branding = models.JSONField(
        default=dict, 
        blank=True,
        help_text="Custom branding configuration (logo, colors, etc.)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this tenant is active"
    )

    stripe_customer_id = models.CharField(
        max_length=255, 
        blank=True, 
        null=True, 
        db_index=True,
        help_text="Stripe Customer ID (cus_...)"
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Tenant'
        verbose_name_plural = 'Tenants'

    def __str__(self):
        return f"{self.name} ({self.subdomain})"


class Plan(UUIDMixin, TimestampMixin):
    """
    Subscription plan that can be assigned to tenants.
    Examples: Basic, Pro, Enterprise
    """
    name = models.CharField(max_length=100, help_text="Plan name")
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Monthly price"
    )
    billing_cycle = models.CharField(
        max_length=20,
        choices=[
            ('monthly', 'Monthly'),
            ('quarterly', 'Quarterly'),
            ('yearly', 'Yearly'),
        ],
        default='monthly',
        help_text="Billing frequency"
    )
    is_public = models.BooleanField(
        default=True,
        help_text="Whether this plan is publicly available"
    )

    stripe_price_id = models.CharField(
        max_length=255, 
        blank=True, 
        null=True,
        help_text="Stripe Price ID (price_...)"
    )

    class Meta:
        ordering = ['price']
        verbose_name = 'Plan'
        verbose_name_plural = 'Plans'

    def __str__(self):
        return f"{self.name} (${self.price}/{self.billing_cycle})"


class Feature(UUIDMixin, TimestampMixin):
    """
    Feature that can be enabled/limited for plans and tenants.
    Examples: max_members, api_access, custom_branding
    """
    key = models.CharField(
        max_length=100,
        unique=True,
        help_text="Unique feature identifier (e.g., 'max_members')"
    )
    description = models.TextField(
        blank=True,
        help_text="Human-readable description of this feature"
    )
    data_type = models.CharField(
        max_length=20,
        choices=[
            ('bool', 'Boolean'),
            ('int', 'Integer'),
            ('string', 'String'),
        ],
        default='bool',
        help_text="Data type for feature value"
    )

    class Meta:
        ordering = ['key']
        verbose_name = 'Feature'
        verbose_name_plural = 'Features'

    def __str__(self):
        return f"{self.key} ({self.data_type})"


class PlanEntitlement(UUIDMixin, TimestampMixin):
    """
    Defines which features are included in a plan and their values.
    Example: Basic plan has max_members=100
    """
    plan = models.ForeignKey(
        Plan,
        on_delete=models.CASCADE,
        related_name='entitlements',
        help_text="Plan this entitlement belongs to"
    )
    feature = models.ForeignKey(
        Feature,
        on_delete=models.CASCADE,
        related_name='plan_entitlements',
        help_text="Feature being entitled"
    )
    value = models.JSONField(
        help_text="Feature value (format depends on feature data_type)"
    )

    class Meta:
        ordering = ['plan', 'feature']
        unique_together = [['plan', 'feature']]
        verbose_name = 'Plan Entitlement'
        verbose_name_plural = 'Plan Entitlements'

    def __str__(self):
        return f"{self.plan.name} - {self.feature.key}: {self.value}"


class TenantSubscription(UUIDMixin, TimestampMixin):
    """
    Active subscription linking a tenant to a plan.
    Tracks subscription status and date range.
    """
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='subscriptions',
        help_text="Tenant with this subscription"
    )
    plan = models.ForeignKey(
        Plan,
        on_delete=models.PROTECT,
        related_name='subscriptions',
        help_text="Subscribed plan"
    )
    status = models.CharField(
        max_length=20,
        choices=[
            ('active', 'Active'),
            ('past_due', 'Past Due'),
            ('canceled', 'Canceled'),
        ],
        default='active',
        help_text="Current subscription status"
    )
    started_at = models.DateTimeField(
        help_text="When subscription started"
    )
    ends_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When subscription ends (null for ongoing)"
    )

    stripe_subscription_id = models.CharField(
        max_length=255, 
        blank=True, 
        null=True,
        db_index=True,
        help_text="Stripe Subscription ID (sub_...)"
    )

    class Meta:
        ordering = ['-started_at']
        verbose_name = 'Tenant Subscription'
        verbose_name_plural = 'Tenant Subscriptions'

    def __str__(self):
        return f"{self.tenant.name} - {self.plan.name} ({self.status})"


class TenantEntitlementOverride(UUIDMixin, TimestampMixin):
    """
    Custom feature override for a specific tenant.
    Allows tenant-specific customization beyond their plan.
    Example: Give a tenant unlimited members even on a Basic plan.
    """
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name='entitlement_overrides',
        help_text="Tenant receiving the override"
    )
    feature = models.ForeignKey(
        Feature,
        on_delete=models.CASCADE,
        related_name='tenant_overrides',
        help_text="Feature being overridden"
    )
    value = models.JSONField(
        help_text="Override value (format depends on feature data_type)"
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this override expires (null for permanent)"
    )

    class Meta:
        ordering = ['tenant', 'feature']
        unique_together = [['tenant', 'feature']]
        verbose_name = 'Tenant Entitlement Override'
        verbose_name_plural = 'Tenant Entitlement Overrides'

    def __str__(self):
        return f"{self.tenant.name} - {self.feature.key}: {self.value}"
