from django.db import models
from .mixins.uuid_mixin import UUIDMixin
from .mixins.timestamps import TimestampMixin
from .mixins.tenant_mixin import TenantMixin

class BaseModel(UUIDMixin, TimestampMixin):
    """
    Base model with UUID and Timestamps.
    """
    class Meta:
        abstract = True

class TenantAwareModel(BaseModel, TenantMixin):
    """
    Base model for all tenant-specific business data.
    Includes UUID, Timestamps, and strict Tenant Isolation.
    """
    class Meta:
        abstract = True