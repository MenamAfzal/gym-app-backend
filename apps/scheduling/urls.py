from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    LocationViewSet, RoomViewSet, StaffLocationViewSet, StaffAvailabilityViewSet,
    ClassTemplateViewSet, RecurrenceRuleViewSet, ClassSessionViewSet, BookingViewSet,
    AppointmentViewSet, WaitlistViewSet, SubstituteRequestViewSet, PackageTypeViewSet,
    PackageViewSet, ReportsView, StaffAssignmentViewSet, ViewAllClientsAPIView,
    FacilityAccessViewSet
)

router = DefaultRouter()
router.register(r'locations', LocationViewSet, basename='location')
router.register(r'rooms', RoomViewSet, basename='room')
router.register(r'staff-locations', StaffLocationViewSet, basename='staff-location')
router.register(r'staff-availability', StaffAvailabilityViewSet, basename='staff-availability')
router.register(r'class-templates', ClassTemplateViewSet, basename='class-template')
router.register(r'recurrence-rules', RecurrenceRuleViewSet, basename='recurrence-rule')
router.register(r'sessions', ClassSessionViewSet, basename='session')
router.register(r'bookings', BookingViewSet, basename='booking')
router.register(r'appointments', AppointmentViewSet, basename='appointment')
router.register(r'waitlist', WaitlistViewSet, basename='waitlist')
router.register(r'substitute-requests', SubstituteRequestViewSet, basename='substitute-request')
router.register(r'package-types', PackageTypeViewSet, basename='package-type')
router.register(r'packages', PackageViewSet, basename='package')
router.register(r'staff-assignments', StaffAssignmentViewSet, basename='staff-assignment')
router.register(r'facility-access', FacilityAccessViewSet, basename='facility-access')



urlpatterns = [
    path('api/view-all-clients/', ViewAllClientsAPIView.as_view(), name='view-all-clients'),
    path('', include(router.urls)),
    path('reports/', ReportsView.as_view(), name='reports'),
]