from django.contrib import admin
from apps.crm.models import TenantCRMConfig, CRMOutboxEvent


@admin.register(TenantCRMConfig)
class TenantCRMConfigAdmin(admin.ModelAdmin):
    list_display = ["tenant", "api_key_id", "crm_base_url", "is_active", "sync_packages_on_save", "updated_at"]
    search_fields = ["tenant__name", "api_key_id"]
    list_filter = ["is_active", "sync_packages_on_save"]


@admin.register(CRMOutboxEvent)
class CRMOutboxEventAdmin(admin.ModelAdmin):
    list_display = ["event_id", "event_type", "tenant", "status", "attempts", "response_status_code", "created_at"]
    search_fields = ["event_id", "event_type", "tenant__name"]
    list_filter = ["status", "event_type", "created_at"]
    readonly_fields = ["id", "event_id", "event_type", "occurred_at", "payload", "created_at", "updated_at"]
