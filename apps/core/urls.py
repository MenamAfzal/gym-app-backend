from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.core.views.billing_views import BillingViewSet
from apps.core.views.platform_views import (
    PlatformTenantViewSet, 
    PlatformPlanViewSet, 
    PlatformFeatureViewSet,
    PlatformReferralRewardViewSet
)
from apps.core.views.webhook_views import StripeWebhookView
from apps.core.views.tenant_analytics import TenantAnalyticsListView

router = DefaultRouter()
router.register(r'tenants', PlatformTenantViewSet, basename='platform-tenants')
router.register(r'plans', PlatformPlanViewSet, basename='platform-plans')
router.register(r'features', PlatformFeatureViewSet, basename='platform-features')
router.register(r'billing', BillingViewSet, basename='tenant-billing')
router.register(r'referrals', PlatformReferralRewardViewSet, basename='platform-referral-rewards')

urlpatterns = [
    # Specific paths MUST come before the router include, otherwise the
    # router's tenants/{pk}/ pattern swallows tenants/analytics/.
    path('tenants/analytics/', TenantAnalyticsListView.as_view(), name='tenant-analytics'),
    path('webhooks/stripe/', StripeWebhookView.as_view(), name='stripe-webhook'),

    # Router URLs (tenants/, plans/, features/, billing/, referrals/)
    path('', include(router.urls)),
]
