import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from apps.core.tenants.models import Tenant, TenantSubscription, TenantEntitlementOverride
from apps.core.tenants.services import TenantEntitlementService

logger = logging.getLogger(__name__)


@receiver(post_save, sender=TenantSubscription)
@receiver(post_delete, sender=TenantSubscription)
def invalidate_subscription_cache(sender, instance, **kwargs):
    if instance.tenant:
        TenantEntitlementService.invalidate_tenant_cache(instance.tenant)


@receiver(post_save, sender=TenantEntitlementOverride)
@receiver(post_delete, sender=TenantEntitlementOverride)
def invalidate_override_cache(sender, instance, **kwargs):
    if instance.tenant:
        TenantEntitlementService.invalidate_tenant_cache(instance.tenant)


@receiver(post_save, sender=Tenant)
def assign_free_plan_on_tenant_creation(sender, instance, created, **kwargs):
    if not created:
        return

    # 1. Seed reflection_logger data
    from apps.reflection_logger.services.seeder import seed_symptoms_for_tenant, seed_focus_for_tenant
    try:
        seed_symptoms_for_tenant(instance)
        seed_focus_for_tenant(instance)
        logger.info("Auto-seeded reflection logger data for tenant '%s'.", instance.name)
    except Exception as e:
        logger.exception("Failed to auto-seed reflection logger data for tenant '%s': %s", instance.name, e)

    # 2. Assign free plan
    from apps.payments.models import BillingPlan, TenantBillingSubscription

    try:
        free_plan = BillingPlan.objects.get(slug=BillingPlan.PlanSlug.FREE)
    except BillingPlan.DoesNotExist:
        logger.warning(
            "Tenant '%s' created but 'free' BillingPlan does not exist.",
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
        "Auto-assigned Free plan to new tenant '%s'.",
        instance.name,
    )