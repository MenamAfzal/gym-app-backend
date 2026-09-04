"""
Reward Engine URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.rewards.views import (
    AdminRewardProgramViewSet, AdminRewardRuleViewSet, AdminBadgeViewSet,
    AdminRewardTierViewSet, AdminRewardCatalogViewSet, AdminRewardRedemptionViewSet,
    AdminRewardWalletViewSet, AdminRewardTransactionViewSet, AdminRewardAnalyticsView,
    AdminRewardRuleVersionViewSet,
    ClientRewardWalletView, ClientRewardLedgerView, ClientBadgeView,
    ClientStreakView, ClientRewardStoreViewSet, ClientRedemptionViewSet
)

router_admin = DefaultRouter()
router_admin.register(r'programs', AdminRewardProgramViewSet, basename='admin-reward-programs')
router_admin.register(r'rules', AdminRewardRuleViewSet, basename='admin-reward-rules')
router_admin.register(r'rule-versions', AdminRewardRuleVersionViewSet, basename='admin-reward-rule-versions')
router_admin.register(r'badges', AdminBadgeViewSet, basename='admin-reward-badges')
router_admin.register(r'tiers', AdminRewardTierViewSet, basename='admin-reward-tiers')
router_admin.register(r'catalog', AdminRewardCatalogViewSet, basename='admin-reward-catalog')
router_admin.register(r'redemptions', AdminRewardRedemptionViewSet, basename='admin-reward-redemptions')
router_admin.register(r'wallets', AdminRewardWalletViewSet, basename='admin-reward-wallets')
router_admin.register(r'transactions', AdminRewardTransactionViewSet, basename='admin-reward-transactions')

router_client = DefaultRouter()
router_client.register(r'store', ClientRewardStoreViewSet, basename='client-reward-store')
router_client.register(r'redemptions', ClientRedemptionViewSet, basename='client-reward-redemptions')

urlpatterns = [
    # Admin Endpoints
    path('admin/analytics/', AdminRewardAnalyticsView.as_view(), name='admin-reward-analytics'),
    path('admin/', include(router_admin.urls)),

    # Client Endpoints
    path('client/wallet/', ClientRewardWalletView.as_view(), name='client-reward-wallet'),
    path('client/history/', ClientRewardLedgerView.as_view(), name='client-reward-history'),
    path('client/badges/', ClientBadgeView.as_view(), name='client-reward-badges'),
    path('client/streaks/', ClientStreakView.as_view(), name='client-reward-streaks'),
    path('client/', include(router_client.urls)),
]
