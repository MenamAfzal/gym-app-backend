from django.contrib import admin
from .models import (
    Location, Room, StaffLocation, StaffAvailability, ClassTemplate,
    RecurrenceRule, ClassSession, Booking, Appointment, Waitlist,
    SubstituteRequest, PackageType, Package, Payment, CancellationPolicy,
    Notification, StaffClientAssignment
)

class TenantAdminMixin:
    """
    Mixin to allow Superusers to see all records across tenants.
    """
    def get_queryset(self, request):
        if request.user.is_superuser:
            return self.model.all_objects.all()
        return super().get_queryset(request)

@admin.register(Location)
class LocationAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'timezone', 'phone', 'tenant')
    search_fields = ('name',)
    list_filter = ('tenant',)

@admin.register(Room)
class RoomAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'location', 'capacity', 'tenant')
    search_fields = ('name', 'location__name')
    list_filter = ('location', 'tenant')

@admin.register(StaffLocation)
class StaffLocationAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('staff', 'location', 'tenant')
    list_filter = ('location', 'tenant')

@admin.register(StaffAvailability)
class StaffAvailabilityAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('staff', 'weekday_or_date', 'start_time', 'end_time', 'is_blackout', 'tenant')
    list_filter = ('is_blackout', 'tenant')
    search_fields = ('staff__email',)

@admin.register(ClassTemplate)
class ClassTemplateAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'location', 'duration_min', 'default_capacity', 'tenant')
    search_fields = ('name',)
    list_filter = ('location', 'tenant')

@admin.register(RecurrenceRule)
class RecurrenceRuleAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('template', 'start_date', 'end_date', 'start_time', 'room', 'staff', 'tenant')
    list_filter = ('start_date', 'end_date', 'tenant')

@admin.register(ClassSession)
class ClassSessionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('template', 'room', 'staff', 'start_at', 'end_at', 'status', 'tenant')
    list_filter = ('status', 'start_at', 'tenant')
    search_fields = ('template__name', 'staff__email')

@admin.register(PackageType)
class PackageTypeAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'price', 'credit_count', 'validity_days', 'location', 'tenant')
    search_fields = ('name',)
    list_filter = ('location', 'tenant')

@admin.register(Package)
class PackageAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('client', 'package_type', 'credits_remaining', 'expires_at', 'tenant')
    list_filter = ('expires_at', 'tenant')
    search_fields = ('client__email', 'package_type__name')
    raw_id_fields = ('client', 'package_type')

@admin.register(Booking)
class BookingAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('session', 'client', 'status', 'join_mode', 'tenant')
    list_filter = ('status', 'join_mode', 'tenant')
    search_fields = ('client__email', 'session__template__name')
    raw_id_fields = ('session', 'client', 'credit_source')

@admin.register(Appointment)
class AppointmentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('client', 'provider', 'start_at', 'end_at', 'status', 'tenant')
    list_filter = ('status', 'start_at', 'tenant')
    search_fields = ('client__email', 'provider__email')

@admin.register(Waitlist)
class WaitlistAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('client', 'session', 'position', 'status', 'tenant')
    list_filter = ('status', 'tenant')

@admin.register(SubstituteRequest)
class SubstituteRequestAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('session', 'requested_by_staff', 'accepted_by_staff', 'status', 'tenant')
    list_filter = ('status', 'tenant')

@admin.register(Payment)
class PaymentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('client', 'amount', 'type', 'status', 'tenant')
    list_filter = ('type', 'status', 'tenant')
    search_fields = ('client__email', 'idempotency_key')

@admin.register(CancellationPolicy)
class CancellationPolicyAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('scope_type', 'template', 'membership_tier', 'cutoff_hours', 'late_fee_amount', 'tenant')
    list_filter = ('scope_type', 'tenant')

@admin.register(Notification)
class NotificationAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('recipient', 'channel', 'template_key', 'sent_at', 'tenant')
    list_filter = ('channel', 'tenant')

@admin.register(StaffClientAssignment)
class StaffClientAssignmentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('staff', 'client', 'tenant')
    search_fields = ('staff__email', 'client__email')
