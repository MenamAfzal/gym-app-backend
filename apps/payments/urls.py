from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import stripe_webhook, StripeConnectOnboardingView, TenantLedgerViewSet, PlatformLedgerViewSet, TriggerPayoutView

router = DefaultRouter()
router.register(r'tenant-ledgers', TenantLedgerViewSet, basename='tenant-ledger')
router.register(r'platform-ledgers', PlatformLedgerViewSet, basename='platform-ledger')

urlpatterns = [
    path('', include(router.urls)),
    path('webhook/stripe/', stripe_webhook, name='stripe-webhook'),
    path('connect/', StripeConnectOnboardingView.as_view(), name='stripe-connect-onboarding'),
    path('trigger-payout/', TriggerPayoutView.as_view(), name='trigger-payout'),
]
