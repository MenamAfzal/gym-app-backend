from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    LocationViewSet, RoomViewSet, SpotTypeViewSet, RoomLayoutViewSet,
    StaffLocationViewSet, StaffAvailabilityViewSet,
    ClassTemplateViewSet, RecurrenceRuleViewSet, ClassSessionViewSet, BookingViewSet,
    AppointmentViewSet, WaitlistViewSet, SubstituteRequestViewSet, PackageTypeViewSet,
    PackageViewSet, ReportsView, StaffAssignmentViewSet, ViewAllClientsAPIView,
    FacilityAccessViewSet, UpdateBookingAttributesAPIView,
    ClientBookingPreferenceView
)

router = DefaultRouter()
router.register(r'locations', LocationViewSet, basename='location')
router.register(r'rooms', RoomViewSet, basename='room')
router.register(r'spot-types', SpotTypeViewSet, basename='spot-type')
router.register(r'layouts', RoomLayoutViewSet, basename='layout')
router.register(r'staff-locations', StaffLocationViewSet, basename='staff-location')
router.register(r'staff-availability', StaffAvailabilityViewSet, basename='staff-availability')
router.register(r'class-templates', ClassTemplateViewSet, basename='class-template')
router.register(r'recurrence-rules', RecurrenceRuleViewSet, basename='recurrence-rule')
router.register(r'sessions', ClassSessionViewSet, basename='session')
router.register(r'events', ClassSessionViewSet, basename='event')
router.register(r'bookings', BookingViewSet, basename='booking')
router.register(r'appointments', AppointmentViewSet, basename='appointment')
router.register(r'waitlist', WaitlistViewSet, basename='waitlist')
router.register(r'substitute-requests', SubstituteRequestViewSet, basename='substitute-request')
router.register(r'package-types', PackageTypeViewSet, basename='package-type')
router.register(r'packages', PackageViewSet, basename='package')
router.register(r'staff-assignments', StaffAssignmentViewSet, basename='staff-assignment')
router.register(r'facility-access', FacilityAccessViewSet, basename='facility-access')

urlpatterns = [
    # Nested Glofox endpoints
    path('locations/<uuid:location_pk>/rooms/', RoomViewSet.as_view({'get': 'list', 'post': 'create'}), name='location-rooms'),
    path('locations/<uuid:location_pk>/spot-types/', SpotTypeViewSet.as_view({'get': 'list', 'post': 'create'}), name='location-spot-types'),
    path('locations/<uuid:location_pk>/spot-types/<uuid:pk>/', SpotTypeViewSet.as_view({
        'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'
    }), name='location-spot-type-detail'),
    path('rooms/<uuid:room_pk>/layouts/', RoomLayoutViewSet.as_view({'get': 'list', 'post': 'create'}), name='room-layouts'),

    path('api/view-all-clients/', ViewAllClientsAPIView.as_view(), name='view-all-clients'),
    path('update-booking-attributes/', UpdateBookingAttributesAPIView.as_view(), name='update-booking-attributes'),
    path('booking-preferences/', ClientBookingPreferenceView.as_view(), name='client-booking-preferences'),
    path('client/booking-preferences/', ClientBookingPreferenceView.as_view(), name='client-booking-preferences-alt'),
    path('preferences/', ClientBookingPreferenceView.as_view(), name='client-preferences'),
    path('', include(router.urls)),
    path('reports/', ReportsView.as_view(), name='reports'),
]