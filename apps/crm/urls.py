from django.urls import path
from apps.crm.views import TenantCRMConfigView, CRMBulkPackageSyncView, CRMOutboxListView

urlpatterns = [
    path("config/", TenantCRMConfigView.as_view(), name="crm-config"),
    path("packages/sync/", CRMBulkPackageSyncView.as_view(), name="crm-packages-sync"),
    path("outbox/", CRMOutboxListView.as_view(), name="crm-outbox-list"),
]
