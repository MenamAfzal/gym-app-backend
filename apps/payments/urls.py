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
    TenantBillingSubscriptionCancelView,
    TenantSubscriptionPlatformFeaturesView,
    PackageCheckoutView,
    PackageCancelView,
    TenantFinanceSummaryAPIView,
    TenantPayoutListView,
    PlatformTenantFinanceBreakdownAPIView,
)
from apps.payments.checkout_dashboard_views import (
    CheckoutCurrenciesListView,
    HealthCommitmentResetDefaultView,
    HealthCommitmentSignatureListView,
    TenantCheckoutConfigAdminView,
    TenantWebsiteIntegrationDashboardView,
    WelcomeTemplateResetDefaultView,
    WidgetEmbedCodeView,
    WidgetPreviewView,
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
        'billing/subscription/platform-features/',
        TenantSubscriptionPlatformFeaturesView.as_view(),
        name='billing-subscription-platform-features',
    ),
    path(
        'billing/subscription/<uuid:subscription_id>/platform-features/',
        TenantSubscriptionPlatformFeaturesView.as_view(),
        name='billing-subscription-platform-features-detail',
    ),
    path(
        'billing/subscription/cancel/',
        TenantBillingSubscriptionCancelView.as_view(),
        name='billing-subscription-cancel',
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

    # ------------------------------------------------------------------ #
    # Financial Ledger Dashboard APIs                                     #
    # ------------------------------------------------------------------ #
    path(
        'tenant/finance/summary/',
        TenantFinanceSummaryAPIView.as_view(),
        name='tenant-finance-summary',
    ),
    path(
        'tenant/payouts/',
        TenantPayoutListView.as_view(),
        name='tenant-payouts',
    ),
    path(
        'platform/finance/tenants/',
        PlatformTenantFinanceBreakdownAPIView.as_view(),
        name='platform-tenant-finance-breakdown',
    ),
    path(
        'checkout-config/',
        TenantCheckoutConfigAdminView.as_view(),
        name='checkout-config-admin',
    ),
    path(
        'checkout-config/currencies/',
        CheckoutCurrenciesListView.as_view(),
        name='checkout-config-currencies',
    ),
    path(
        'checkout-config/health-commitment/reset/',
        HealthCommitmentResetDefaultView.as_view(),
        name='checkout-config-health-commitment-reset',
    ),
    path(
        'checkout-config/health-commitment/signatures/',
        HealthCommitmentSignatureListView.as_view(),
        name='checkout-config-health-commitment-signatures',
    ),
    path(
        'checkout-config/welcome/reset/',
        WelcomeTemplateResetDefaultView.as_view(),
        name='checkout-config-welcome-reset',
    ),
    path(
        'checkout-config/preview/',
        WidgetPreviewView.as_view(),
        name='checkout-config-preview',
    ),
    path(
        'checkout-config/embed-snippet/',
        WidgetEmbedCodeView.as_view(),
        name='checkout-config-embed-snippet',
    ),
    path(
        'checkout-config/dashboard-ui/',
        TenantWebsiteIntegrationDashboardView.as_view(),
        name='checkout-config-dashboard-ui',
    ),
]
