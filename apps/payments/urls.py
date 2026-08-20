from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    # Legacy / existing views
    stripe_webhook,
    StripeConnectOnboardingView,
    TenantLedgerViewSet,
    PlatformLedgerViewSet,
    TriggerPayoutView,
    # New feature-based billing views
    CreateCheckoutSessionView,
    stripe_checkout_webhook,
    BillingFeatureListView,
    BillingFeatureDetailView,
    BillingPlanListView,
    TenantBillingSubscriptionView,
    PackageCheckoutView,
    PackageCancelView,
)

router = DefaultRouter()
router.register(r'tenant-ledgers', TenantLedgerViewSet, basename='tenant-ledger')
router.register(r'platform-ledgers', PlatformLedgerViewSet, basename='platform-ledger')

urlpatterns = [
    path('', include(router.urls)),

    # ------------------------------------------------------------------ #
    # Existing endpoints (unchanged)                                       #
    # ------------------------------------------------------------------ #
    path('webhook/stripe/', stripe_webhook, name='stripe-webhook'),
    path('connect/', StripeConnectOnboardingView.as_view(), name='stripe-connect-onboarding'),
    path('trigger-payout/', TriggerPayoutView.as_view(), name='trigger-payout'),

    path(
        'billing/checkout/',
        CreateCheckoutSessionView.as_view(),
        name='billing-checkout',
    ),

    path(
        'webhook/stripe/checkout/',
        stripe_checkout_webhook,
        name='stripe-checkout-webhook',
    ),

    path(
        'billing/features/',
        BillingFeatureListView.as_view(),
        name='billing-feature-list',
    ),
    path(
        'billing/features/<uuid:pk>/',
        BillingFeatureDetailView.as_view(),
        name='billing-feature-detail',
    ),
    path(
        'billing/plans/',
        BillingPlanListView.as_view(),
        name='billing-plan-list',
    ),

    path(
        'billing/subscription/',
        TenantBillingSubscriptionView.as_view(),
        name='billing-subscription',
    ),
    path(
        'packages/checkout/',
        PackageCheckoutView.as_view(),
        name='package-checkout',
    ),
    path(
        'packages/<uuid:package_id>/cancel/',
        PackageCancelView.as_view(),
        name='package-cancel',
    ),
]
