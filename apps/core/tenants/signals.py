"""
Tenant Signals

Automated cache invalidation for tenant entitlements.
Ensures that plan changes or overrides take effect immediately.
"""
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from apps.core.tenants.models import TenantSubscription, TenantEntitlementOverride
from apps.core.tenants.services import TenantEntitlementService

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
        