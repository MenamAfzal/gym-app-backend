from django.db import models
from django.utils import timezone
from apps.core.tenants.context import get_current_tenant, is_isolation_bypassed
from core_models.mixins.tenant_mixin import TenantAwareManager


class SoftDeleteQuerySet(models.QuerySet):
    """
    QuerySet providing filtering for active vs soft-deleted entities.
    """
    def alive(self):
        return self.filter(is_deleted=False)

    def deleted(self):
        return self.filter(is_deleted=True)


class TenantSoftDeleteManager(TenantAwareManager.from_queryset(SoftDeleteQuerySet)):
    """
    Combines multi-tenant scoping with soft-delete filtering capabilities.
    """
    pass


class SoftDeleteModel(models.Model):
    """
    Abstract mixin supporting soft deletion and restoration.
    """
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):
        """
        Soft-delete this instance by setting is_deleted=True and timestamping deleted_at.
        """
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at"])

    def hard_delete(self, using=None, keep_parents=False):
        """
        Permanently remove record from database if explicitly needed.
        """
        return super().delete(using=using, keep_parents=keep_parents)

    def restore(self):
        """
        Restore a soft-deleted instance.
        """
        self.is_deleted = False
        self.deleted_at = None
        self.save(update_fields=["is_deleted", "deleted_at"])
