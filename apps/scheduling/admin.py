from django.contrib import admin
from .models import StaffClientAssignment, PricingOption, ClientPass, Session, Booking

class TenantAdminMixin:
    """
    Mixin to allow Superusers to see all records across tenants.
    Regular admin users are still scoped by the middleware/manager.
    """
    def get_queryset(self, request):
        # If superuser or no tenant context set (e.g. localhost direct access), 
        # try to show everything to avoid "empty admin" confusion.
        if request.user.is_superuser:
            return self.model.all_objects.all()
        return super().get_queryset(request)

@admin.register(StaffClientAssignment)
class StaffClientAssignmentAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('staff', 'client', 'created_at', 'tenant')
    search_fields = ('staff__email', 'client__email')
    list_filter = ('staff', 'tenant')

@admin.register(PricingOption)
class PricingOptionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('name', 'price', 'session_credits', 'duration_days', 'tenant')
    search_fields = ('name',)
    list_filter = ('tenant',)

@admin.register(ClientPass)
class ClientPassAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('client', 'pricing_option', 'credits_remaining', 'expiry_date', 'is_active', 'tenant')
    list_filter = ('is_active', 'expiry_date', 'tenant')
    search_fields = ('client__email', 'pricing_option__name')
    raw_id_fields = ('client', 'pricing_option')

@admin.register(Session)
class SessionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('title', 'staff', 'start_time', 'end_time', 'capacity', 'session_type', 'tenant')
    list_filter = ('start_time', 'session_type', 'staff', 'tenant')
    search_fields = ('title', 'staff__email')
    raw_id_fields = ('staff',)

@admin.register(Booking)
class BookingAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('session', 'client', 'status', 'join_mode', 'tenant')
    list_filter = ('status', 'join_mode', 'tenant')
    search_fields = ('client__email', 'session__title')
    raw_id_fields = ('session', 'client', 'used_pass')
