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


from apps.payments.models import TenantBillingSubscription
from django.db.models.signals import m2m_changed


@receiver(post_save, sender=TenantBillingSubscription)
@receiver(post_delete, sender=TenantBillingSubscription)
def invalidate_billing_subscription_cache(sender, instance, **kwargs):
    if getattr(instance, 'tenant', None):
        TenantEntitlementService.invalidate_tenant_cache(instance.tenant)


@receiver(m2m_changed, sender=TenantBillingSubscription.active_features.through)
def invalidate_billing_features_m2m_cache(sender, instance, **kwargs):
    tenant = getattr(instance, 'tenant', None)
    if tenant:
        TenantEntitlementService.invalidate_tenant_cache(tenant)


@receiver(post_save, sender=Tenant)
def on_tenant_creation(sender, instance, created, **kwargs):
    if not created:
        return

    from apps.core.tenants.provisioner import TenantProvisioningService
    TenantProvisioningService.provision_new_tenant(instance)