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


class CRMOutboxListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CRMOutboxEventSerializer

    def get_queryset(self):
        tenant = get_current_tenant() or getattr(self.request.user, "tenant", None)
        if not tenant:
            return CRMOutboxEvent.objects.none()
        return CRMOutboxEvent.objects.filter(tenant=tenant).order_by("-created_at")
