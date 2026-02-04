from django.contrib import admin
from .models import StaffClientAssignment, PricingOption, ClientPass, Session, Booking

@admin.register(StaffClientAssignment)
class StaffClientAssignmentAdmin(admin.ModelAdmin):
    list_display = ('staff', 'client', 'created_at')
    search_fields = ('staff__email', 'client__email')
    list_filter = ('staff',)

@admin.register(PricingOption)
class PricingOptionAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'session_credits', 'duration_days')
    search_fields = ('name',)

@admin.register(ClientPass)
class ClientPassAdmin(admin.ModelAdmin):
    list_display = ('client', 'pricing_option', 'credits_remaining', 'expiry_date', 'is_active')
    list_filter = ('is_active', 'expiry_date')
    search_fields = ('client__email', 'pricing_option__name')
    raw_id_fields = ('client', 'pricing_option')

@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ('title', 'staff', 'start_time', 'end_time', 'capacity', 'session_type')
    list_filter = ('start_time', 'session_type', 'staff')
    search_fields = ('title', 'staff__email')
    raw_id_fields = ('staff',)

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('session', 'client', 'status', 'join_mode')
    list_filter = ('status', 'join_mode')
    search_fields = ('client__email', 'session__title')
    raw_id_fields = ('session', 'client', 'used_pass')
