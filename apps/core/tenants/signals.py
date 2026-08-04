"""
Tenant Signals

1. Cache invalidation for tenant entitlements — invalidates Redis cache when a
   subscription or entitlement override changes.

2. Free plan auto-assignment — whenever a new Tenant (gym) is created, a Free
   plan TenantBillingSubscription is automatically provisioned in the payments
   app.  This requires no Stripe interaction and happens entirely in the DB.
"""
import logging

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from apps.core.tenants.models import Tenant, TenantSubscription, TenantEntitlementOverride
from apps.core.tenants.services import TenantEntitlementService

logger = logging.getLogger(__name__)


@receiver(post_save, sender=TenantSubscription)
@receiver(post_delete, sender=TenantSubscription)
def invalidate_subscription_cache(sender, instance, **kwargs):
    """
    Invalidate tenant cache when subscription changes.
    """
    if instance.tenant:
        TenantEntitlementService.invalidate_tenant_cache(instance.tenant)


@receiver(post_save, sender=TenantEntitlementOverride)
@receiver(post_delete, sender=TenantEntitlementOverride)
def invalidate_override_cache(sender, instance, **kwargs):
    """
    Invalidate tenant cache when overrides change.
    """
    if instance.tenant:
        TenantEntitlementService.invalidate_tenant_cache(instance.tenant)



@receiver(post_save, sender=Tenant)
def assign_free_plan_on_tenant_creation(sender, instance, created, **kwargs):
    """
    Automatically create a Free-tier ``TenantBillingSubscription`` whenever a
    new Tenant (gym) is onboarded.

    This signal fires after ``Tenant.objects.create(...)`` and ensures every
    gym starts with a valid billing record requiring no Stripe interaction.

    If the 'free' BillingPlan has not been seeded in the database yet (e.g.
    during initial migrations), a warning is logged and no subscription is
    created — the platform admin can create it manually via Django Admin.
    """
    if not created:
        return  # Only act on new Tenant records

    from apps.payments.models import BillingPlan, TenantBillingSubscription

    try:
        free_plan = BillingPlan.objects.get(slug=BillingPlan.PlanSlug.FREE)
    except BillingPlan.DoesNotExist:
        logger.warning(
            "Tenant '%s' was created but no 'free' BillingPlan exists in the DB. "
            "Seed BillingPlan data and create the subscription manually via Admin.",
            instance.name,
        )
        return

    if TenantBillingSubscription.objects.filter(tenant=instance).exists():
        return

    TenantBillingSubscription.objects.create(
        tenant=instance,
        billing_plan=free_plan,
        status=TenantBillingSubscription.StatusChoices.ACTIVE,
    )
    logger.info(
        "Auto-assigned Free plan to new tenant '%s' (id=%s).",
        instance.name,
        instance.id,
    )