from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ClientRetentionMetricsViewSet, SavedSegmentViewSet

router = DefaultRouter()
router.register(r'metrics', ClientRetentionMetricsViewSet, basename='retention-metrics')
router.register(r'segments', SavedSegmentViewSet, basename='retention-segments')

urlpatterns = [
    path('', include(router.urls)),
]
