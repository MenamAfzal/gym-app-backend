from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.core.views.billing_views import BillingViewSet
from apps.core.views.platform_views import (
    PlatformTenantViewSet, 
    PlatformPlanViewSet, 
    PlatformFeatureViewSet
)
from apps.core.views.webhook_views import StripeWebhookView

router = DefaultRouter()
router.register(r'tenants', PlatformTenantViewSet, basename='platform-tenants')
router.register(r'plans', PlatformPlanViewSet, basename='platform-plans')
router.register(r'features', PlatformFeatureViewSet, basename='platform-features')
router.register(r'billing', BillingViewSet, basename='tenant-billing')

urlpatterns = [
     # Platform Routes
    path('', include(router.urls)), 

    path('webhooks/stripe/', StripeWebhookView.as_view(), name='stripe-webhook'),
]
