from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ClientRetentionMetricsViewSet,
    SavedSegmentViewSet,
    ClientActivityTimelineView,
    RetentionOverviewDashboardView,
)

router = DefaultRouter()
router.register(r'metrics', ClientRetentionMetricsViewSet, basename='retention-metrics')
router.register(r'segments', SavedSegmentViewSet, basename='retention-segments')

urlpatterns = [
    path('clients/<uuid:client_id>/timeline/', ClientActivityTimelineView.as_view(), name='retention-client-timeline'),
    path('dashboard/overview/', RetentionOverviewDashboardView.as_view(), name='retention-dashboard-overview'),
    path('', include(router.urls)),
]
