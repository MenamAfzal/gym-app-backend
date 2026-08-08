from django.db import models
from apps.core.tenants.context import get_current_tenant, is_isolation_bypassed

class TenantAwareManager(models.Manager):
    """
    Manager that automatically filters queries by the current tenant.
    Prevents data leakage across tenants.
    """
    
    def get_queryset(self):
        """
        Override get_queryset to filter by the active tenant.
        """
        queryset = super().get_queryset()
        
        # Explicit bypass for cross-tenant operations (e.g. Platform Admin)
        if is_isolation_bypassed():
            return queryset
            
        tenant = get_current_tenant()
        
        if tenant:
            # If we have a tenant in context, filter by it
            return queryset.filter(tenant=tenant)
        
        # If no tenant is active and bypass is not set, strictly return none
        # to prevent data leakage.
        return queryset.none()

class TenantMixin(models.Model):
    """
    Abstract mixin to make a model tenant-aware.
    Adds a ForeignKey to Tenant and the TenantAwareManager.
    """
    
    # Proper Foreign Key linking to the Tenant model
    # We use string reference to avoid circular imports
    tenant = models.ForeignKey(
        'tenants.Tenant', 
        on_delete=models.CASCADE,
        related_name='%(app_label)s_%(class)ss', # e.g. tenant.workout_workouts
        help_text="The tenant this record belongs to"
    )

    objects = TenantAwareManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True
        
    def save(self, *args, **kwargs):
        """
        Auto-assign tenant on save if not present.
        """
        if not self.tenant_id:
            current_tenant = get_current_tenant()
            if current_tenant:
                self.tenant = current_tenant
        super().save(*args, **kwargs)
        