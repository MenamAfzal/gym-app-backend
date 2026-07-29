from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import TenantTicketViewSet, PlatformTicketViewSet

router = DefaultRouter()
router.register(r'tickets', TenantTicketViewSet, basename='tenant-ticket')
router.register(r'platform-tickets', PlatformTicketViewSet, basename='platform-ticket')

urlpatterns = [
    path('', include(router.urls)),
]
