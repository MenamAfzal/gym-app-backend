"""
Affiliate URL Configuration
"""
from django.urls import path
from apps.core.tenants.affiliate_views import (
    AffiliateRegisterView,
    AffiliateResolveView,
    AffiliateDashboardView,
    AffiliateHistoryView,
    AffiliateReferredGymsView,
    AffiliateJoinLandingView
)

urlpatterns = [
    path('register/', AffiliateRegisterView.as_view(), name='affiliate-register'),
    path('resolve/', AffiliateResolveView.as_view(), name='affiliate-resolve'),
    path('join-gym/', AffiliateJoinLandingView.as_view(), name='affiliate-join-landing-api'),
    path('join-gym', AffiliateJoinLandingView.as_view(), name='affiliate-join-landing-api-no-slash'),
    path('me/dashboard/', AffiliateDashboardView.as_view(), name='affiliate-dashboard'),
    path('me/history/', AffiliateHistoryView.as_view(), name='affiliate-history'),
    path('me/gyms/', AffiliateReferredGymsView.as_view(), name='affiliate-gyms'),
]
