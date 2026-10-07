from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ClientRetentionMetricsViewSet,
    SavedSegmentViewSet,
    ClientActivityTimelineView,
    RetentionOverviewDashboardView,
    CustomerJourneyFunnelView,
    CohortRetentionMatrixView,
    ClassUtilizationAnalyticsView,
    StaffPerformanceAnalyticsView,
    AtRiskSummaryView,
)

router = DefaultRouter()
router.register(r'metrics', ClientRetentionMetricsViewSet, basename='retention-metrics')
router.register(r'segments', SavedSegmentViewSet, basename='retention-segments')

urlpatterns = [
    path('clients/<uuid:client_id>/timeline/', ClientActivityTimelineView.as_view(), name='retention-client-timeline'),
    path('dashboard/overview/', RetentionOverviewDashboardView.as_view(), name='retention-dashboard-overview'),

    # Phase 2 Analytics Endpoints
    path('analytics/funnel/', CustomerJourneyFunnelView.as_view(), name='retention-analytics-funnel'),
    path('analytics/cohorts/', CohortRetentionMatrixView.as_view(), name='retention-analytics-cohorts'),
    path('analytics/class-utilization/', ClassUtilizationAnalyticsView.as_view(), name='retention-analytics-class-utilization'),
    path('analytics/staff-performance/', StaffPerformanceAnalyticsView.as_view(), name='retention-analytics-staff-performance'),

    # Phase 2 At-Risk Summary
    path('at-risk/summary/', AtRiskSummaryView.as_view(), name='retention-at-risk-summary'),

    path('', include(router.urls)),
]
