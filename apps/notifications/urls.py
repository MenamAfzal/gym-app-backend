from django.urls import path
from .views import (
    FCMDeviceRegisterAPIView,
    NotificationListAPIView,
    NotificationMarkReadAPIView
)

urlpatterns = [
    path('devices/register/', FCMDeviceRegisterAPIView.as_view(), name='device-register'),
    path('', NotificationListAPIView.as_view(), name='notification-list'),
    path('read-all/', NotificationMarkReadAPIView.as_view(), name='notification-read-all'),
    path('<uuid:pk>/read/', NotificationMarkReadAPIView.as_view(), name='notification-mark-read'),
]
