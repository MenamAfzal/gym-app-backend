"""
Notification Engine — URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    FCMDeviceViewSet,
    NotificationInboxViewSet,
    NotificationCampaignViewSet,
    NotificationTemplateViewSet,
    NotificationGroupViewSet,
    NotificationAutomationViewSet,
    NotificationPreferenceView,
    TenantNotificationSettingsView,
)

router = DefaultRouter()
router.register(r'devices',    FCMDeviceViewSet,              basename='notification-device')
router.register(r'inbox',      NotificationInboxViewSet,      basename='notification-inbox')
router.register(r'campaigns',  NotificationCampaignViewSet,   basename='notification-campaign')
router.register(r'templates',  NotificationTemplateViewSet,   basename='notification-template')
router.register(r'groups',     NotificationGroupViewSet,      basename='notification-group')
router.register(r'automations', NotificationAutomationViewSet, basename='notification-automation')

urlpatterns = [
    # Router-managed ViewSets
    path('', include(router.urls)),

    # Singleton views (preferences + settings)
    path('preferences/', NotificationPreferenceView.as_view(), name='notification-preferences'),
    path('settings/',    TenantNotificationSettingsView.as_view(), name='notification-settings'),
]
