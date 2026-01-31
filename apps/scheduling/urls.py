from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SessionViewSet, 
    BookingViewSet, 
    StaffAssignmentViewSet,
    PricingOptionViewSet, # (Added below)
    ClientPassViewSet     # (Added below)
)

router = DefaultRouter()
router.register(r'sessions', SessionViewSet, basename='session')
router.register(r'bookings', BookingViewSet, basename='booking')
router.register(r'staff-assignments', StaffAssignmentViewSet, basename='staff-assignment')
router.register(r'pricing-options', PricingOptionViewSet, basename='pricing-option')
router.register(r'client-passes', ClientPassViewSet, basename='client-pass')

urlpatterns = [
    path('', include(router.urls)),
]
    