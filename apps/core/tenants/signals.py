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
def on_tenant_creation(sender, instance, created, **kwargs):
    if not created:
        return

    from apps.core.tenants.provisioner import TenantProvisioningService
    TenantProvisioningService.provision_new_tenant(instance)