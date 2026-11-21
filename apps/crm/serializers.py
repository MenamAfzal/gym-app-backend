from rest_framework import serializers
from apps.crm.models import TenantCRMConfig, CRMOutboxEvent


class TenantCRMConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantCRMConfig
        fields = [
            "id",
            "api_key_id",
            "api_secret",
            "crm_base_url",
            "is_active",
            "sync_packages_on_save",
            "stop_lead_workflow_name",
            "start_onboarding_workflow_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "api_secret": {"write_only": False},
        }


class CRMOutboxEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = CRMOutboxEvent
        fields = [
            "id",
            "event_id",
            "event_type",
            "occurred_at",
            "payload",
            "status",
            "attempts",
            "last_attempt_at",
            "response_status_code",
            "response_body",
            "error_message",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
