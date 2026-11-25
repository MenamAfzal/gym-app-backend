from django.urls import path
from apps.crm.views import (
    TenantCRMConfigView,
    CRMBulkPackageSyncView,
    CRMBrandingSyncView,
    CRMOutboxListView,
)

urlpatterns = [
    path("config/", TenantCRMConfigView.as_view(), name="crm-config"),
    path("packages/sync/", CRMBulkPackageSyncView.as_view(), name="crm-packages-sync"),
    path("branding/", CRMBrandingSyncView.as_view(), name="crm-branding"),
    path("branding/sync/", CRMBrandingSyncView.as_view(), name="crm-branding-sync"),
    path("outbox/", CRMOutboxListView.as_view(), name="crm-outbox-list"),
]
