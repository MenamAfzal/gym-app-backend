import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.core.tenants.models import Tenant
from apps.payments.models import TenantCheckoutConfig
from apps.scheduling.models import PackageType, Appointment
from apps.users.models import User, UserProfile, UserRole
from apps.crm.services import CRMIntegrationService

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Tenant)
def auto_sync_tenant_branding_to_crm(sender, instance, created, **kwargs):
    """
    Automatically synchronizes Tenant branding colors and logo to the external CRM when updated.
    """
    config = CRMIntegrationService.get_config_for_tenant(instance)
    if not config or not config.is_configured:
        return

    try:
        CRMIntegrationService.sync_branding_to_crm(instance)
    except Exception as exc:
        logger.warning("CRM branding auto-sync failed for Tenant %s: %s", instance.id, exc)


@receiver(post_save, sender=TenantCheckoutConfig)
def auto_sync_checkout_branding_to_crm(sender, instance, created, **kwargs):
    """
    Automatically synchronizes theme_primary_color updates on TenantCheckoutConfig to the external CRM.
    """
    tenant = getattr(instance, "tenant", None)
    if not tenant:
        return

    config = CRMIntegrationService.get_config_for_tenant(tenant)
    if not config or not config.is_configured:
        return

    try:
        CRMIntegrationService.sync_branding_to_crm(tenant)
    except Exception as exc:
        logger.warning("CRM checkout branding auto-sync failed for Tenant %s: %s", tenant.id, exc)


@receiver(post_save, sender=PackageType)
def auto_sync_package_type_to_crm(sender, instance, created, **kwargs):
    """
    Automatically synchronizes PackageType updates to the external CRM when enabled.
    """
    tenant = getattr(instance, "tenant", None)
    if not tenant:
        return

    config = CRMIntegrationService.get_config_for_tenant(tenant)
    if not config or not config.is_configured or not config.sync_packages_on_save:
        return

    try:
        CRMIntegrationService.sync_package_to_crm(instance)
    except Exception as exc:
        logger.warning("CRM auto-sync failed for PackageType %s: %s", instance.id, exc)


@receiver(post_save, sender=Appointment)
def auto_sync_appointment_to_crm(sender, instance, created, **kwargs):
    """
    Optionally dispatches appointment lifecycle events to CRM.
    """
    tenant = getattr(instance, "tenant", None)
    if not tenant:
        return

    config = CRMIntegrationService.get_config_for_tenant(tenant)
    if not config or not config.is_configured:
        return

    try:
        if created:
            CRMIntegrationService.dispatch_appointment_event(instance, "booked")
        elif instance.status in ("cancelled", "rescheduled", "completed"):
            CRMIntegrationService.dispatch_appointment_event(instance, instance.status)
    except Exception as exc:
        logger.warning("CRM appointment sync failed for Appointment %s: %s", instance.id, exc)


@receiver(post_save, sender=UserProfile)
def auto_sync_client_profile_to_crm(sender, instance, created, **kwargs):
    """
    Dispatches customer.updated to CRM when client profile information changes.
    """
    user = getattr(instance, "user", None)
    if not user or getattr(user, "role", None) != UserRole.CLIENT:
        return

    tenant = getattr(user, "tenant", None)
    if not tenant:
        return

    config = CRMIntegrationService.get_config_for_tenant(tenant)
    if not config or not config.is_configured:
        return

    try:
        CRMIntegrationService.dispatch_customer_updated_event(user)
    except Exception as exc:
        logger.warning("CRM customer update dispatch failed for User %s: %s", user.id, exc)
