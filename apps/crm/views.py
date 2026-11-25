from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView

from apps.core.tenants.context import get_current_tenant
from apps.crm.models import TenantCRMConfig, CRMOutboxEvent
from apps.crm.serializers import TenantCRMConfigSerializer, CRMOutboxEventSerializer
from apps.crm.services import CRMIntegrationService


class TenantCRMConfigView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = get_current_tenant() or getattr(request.user, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant context required"}, status=status.HTTP_400_BAD_REQUEST)

        config, _ = TenantCRMConfig.objects.get_or_create(tenant=tenant)
        serializer = TenantCRMConfigSerializer(config)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request):
        tenant = get_current_tenant() or getattr(request.user, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant context required"}, status=status.HTTP_400_BAD_REQUEST)

        config, _ = TenantCRMConfig.objects.get_or_create(tenant=tenant)
        serializer = TenantCRMConfigSerializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class CRMBulkPackageSyncView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant = get_current_tenant() or getattr(request.user, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant context required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            success, result = CRMIntegrationService.sync_all_packages_to_crm(tenant)
            status_code = status.HTTP_200_OK if success else status.HTTP_502_BAD_GATEWAY
            return Response(result, status=status_code)
        except Exception as exc:
            return Response(
                {"error": str(exc), "detail": "An error occurred while communicating with CRM service."},
                status=status.HTTP_502_BAD_GATEWAY
            )


class CRMBrandingSyncView(APIView):
    permission_classes = [IsAuthenticated]

    def _update_tenant_branding_from_request(self, tenant, request) -> bool:
        """
        Optionally updates tenant.logo and tenant.branding if fields are provided in request.data / request.FILES.
        Returns True if tenant was modified and saved.
        """
        data = request.data if hasattr(request, "data") and isinstance(request.data, dict) else {}
        files = getattr(request, "FILES", None)
        modified = False

        if files and "logo" in files:
            tenant.logo = files["logo"]
            modified = True

        branding_dict = dict(tenant.branding) if isinstance(tenant.branding, dict) else {}
        if isinstance(data.get("branding"), dict):
            branding_dict.update(data["branding"])
            modified = True

        color_keys = (
            "primary_color",
            "secondary_color",
            "accent_color",
            "background_color",
            "text_color",
            "logo_url",
        )
        for key in color_keys:
            if key in data and data[key] is not None:
                branding_dict[key] = data[key]
                modified = True

        if isinstance(data.get("colors"), dict):
            existing_colors = branding_dict.get("colors") if isinstance(branding_dict.get("colors"), dict) else {}
            existing_colors.update(data["colors"])
            branding_dict["colors"] = existing_colors
            for key in ("primary_color", "secondary_color", "accent_color", "background_color", "text_color"):
                if key in existing_colors and existing_colors[key]:
                    branding_dict[key] = existing_colors[key]
            modified = True

        if modified:
            tenant.branding = branding_dict
            tenant.save()
            # Keep checkout theme_primary_color aligned if checkout_config exists
            primary_color = branding_dict.get("primary_color")
            if primary_color:
                try:
                    checkout_cfg = getattr(tenant, "checkout_config", None)
                    if checkout_cfg and checkout_cfg.theme_primary_color != primary_color:
                        checkout_cfg.theme_primary_color = primary_color
                        checkout_cfg.save(update_fields=["theme_primary_color"])
                except Exception:
                    pass
        return modified

    def get(self, request):
        tenant = get_current_tenant() or getattr(request.user, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant context required"}, status=status.HTTP_400_BAD_REQUEST)

        payload = CRMIntegrationService.serialize_tenant_branding(tenant)
        return Response(payload, status=status.HTTP_200_OK)

    def post(self, request):
        tenant = get_current_tenant() or getattr(request.user, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant context required"}, status=status.HTTP_400_BAD_REQUEST)

        self._update_tenant_branding_from_request(tenant, request)
        branding_payload = CRMIntegrationService.serialize_tenant_branding(tenant)

        try:
            success, result = CRMIntegrationService.sync_branding_to_crm(tenant)
            status_code = status.HTTP_200_OK if success else status.HTTP_502_BAD_GATEWAY
            return Response(
                {
                    "synced": success,
                    "branding": branding_payload,
                    "crm_response": result,
                },
                status=status_code,
            )
        except Exception as exc:
            return Response(
                {
                    "synced": False,
                    "branding": branding_payload,
                    "error": str(exc),
                    "detail": "An error occurred while communicating with CRM service.",
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

    def put(self, request):
        return self.post(request)

    def patch(self, request):
        return self.post(request)


class CRMOutboxListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CRMOutboxEventSerializer

    def get_queryset(self):
        tenant = get_current_tenant() or getattr(self.request.user, "tenant", None)
        if not tenant:
            return CRMOutboxEvent.objects.none()
        return CRMOutboxEvent.objects.filter(tenant=tenant).order_by("-created_at")
