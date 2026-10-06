import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.users.models import UserRole
from apps.scheduling.permissions import IsOwnerOrManager
from .models import ClientRetentionMetrics, SavedSegment
from .serializers import ClientRetentionMetricsSerializer, SavedSegmentSerializer
from .services import RetentionMetricsService

logger = logging.getLogger(__name__)


class ClientRetentionMetricsViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing and recalculating client retention metrics.
    """
    queryset = ClientRetentionMetrics.objects.all().select_related('client', 'tenant')
    serializer_class = ClientRetentionMetricsSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    filterset_fields = ['risk_level', 'attendance_trend']
    search_fields = ['client__email', 'client__first_name', 'client__last_name']
    ordering_fields = ['churn_risk_score', 'days_since_last_visit', 'lifetime_value', 'visits_last_30d']

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return ClientRetentionMetrics.objects.none()

        qs = ClientRetentionMetrics.objects.select_related('client', 'tenant')
        client_id = self.request.query_params.get('client_id')
        if client_id:
            qs = qs.filter(client_id=client_id)
        risk_level = self.request.query_params.get('risk_level')
        if risk_level:
            qs = qs.filter(risk_level=risk_level)
        return qs

    @action(detail=False, methods=['post'], url_path='recalculate')
    def recalculate(self, request):
        """
        Manually trigger recalculation of retention metrics for the current tenant or specific client.
        """
        tenant_id = getattr(request, 'tenant_id', None) or getattr(request.user, 'tenant_id', None)
        if not tenant_id:
            return Response(
                {"detail": "No tenant associated with the current request."},
                status=status.HTTP_400_BAD_REQUEST
            )

        client_id = request.data.get('client_id')
        client_ids = [client_id] if client_id else None

        processed = RetentionMetricsService.recalculate_for_tenant(
            tenant_id=str(tenant_id),
            client_ids=client_ids
        )
        return Response({
            "status": "success",
            "message": f"Successfully recalculated retention metrics for {processed} clients.",
            "processed_count": processed
        }, status=status.HTTP_200_OK)


class SavedSegmentViewSet(viewsets.ModelViewSet):
    """
    CRUD API for Momence-style Saved Customer Segments and dynamic cohorts.
    """
    queryset = SavedSegment.objects.all().select_related('created_by', 'tenant')
    serializer_class = SavedSegmentSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrManager]

    def get_queryset(self):
        return SavedSegment.objects.select_related('created_by', 'tenant')

    def perform_create(self, serializer):
        tenant = getattr(self.request, 'tenant', None) or getattr(self.request.user, 'tenant', None)
        serializer.save(
            tenant=tenant,
            created_by=self.request.user
        )
