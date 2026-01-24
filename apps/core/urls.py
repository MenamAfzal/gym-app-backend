from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.core.views.platform_views import (
    PlatformTenantViewSet, 
    PlatformPlanViewSet, 
    PlatformFeatureViewSet
)

router = DefaultRouter()
router.register(r'tenants', PlatformTenantViewSet, basename='platform-tenants')
router.register(r'plans', PlatformPlanViewSet, basename='platform-plans')
router.register(r'features', PlatformFeatureViewSet, basename='platform-features')

urlpatterns = [
     # Platform Routes
    path('', include(router.urls)), 
]
