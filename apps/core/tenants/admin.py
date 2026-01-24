"""
Tenant Admin Configuration
"""
from django.contrib import admin
from .models import (
    Tenant, Plan, Feature, PlanEntitlement,
    TenantSubscription, TenantEntitlementOverride
)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """Admin for Tenant model."""
    list_display = ['name', 'subdomain', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'subdomain']
    readonly_fields = ['id', 'created_at', 'updated_at']
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'subdomain', 'is_active')
        }),
        ('Branding', {
            'fields': ('branding',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    """Admin for Plan model."""
    list_display = ['name', 'price', 'billing_cycle', 'is_public', 'created_at']
    list_filter = ['billing_cycle', 'is_public', 'created_at']
    search_fields = ['name']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(Feature)
class FeatureAdmin(admin.ModelAdmin):
    """Admin for Feature model."""
    list_display = ['key', 'data_type', 'description', 'created_at']
    list_filter = ['data_type', 'created_at']
    search_fields = ['key', 'description']
    readonly_fields = ['id', 'created_at', 'updated_at']


@admin.register(PlanEntitlement)
class PlanEntitlementAdmin(admin.ModelAdmin):
    """Admin for PlanEntitlement model."""
    list_display = ['plan', 'feature', 'value', 'created_at']
    list_filter = ['plan', 'created_at']
    search_fields = ['plan__name', 'feature__key']
    readonly_fields = ['id', 'created_at', 'updated_at']
    autocomplete_fields = ['plan', 'feature']


@admin.register(TenantSubscription)
class TenantSubscriptionAdmin(admin.ModelAdmin):
    """Admin for TenantSubscription model."""
    list_display = ['tenant', 'plan', 'status', 'started_at', 'ends_at']
    list_filter = ['status', 'started_at', 'ends_at']
    search_fields = ['tenant__name', 'plan__name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    autocomplete_fields = ['tenant', 'plan']
    date_hierarchy = 'started_at'


@admin.register(TenantEntitlementOverride)
class TenantEntitlementOverrideAdmin(admin.ModelAdmin):
    """Admin for TenantEntitlementOverride model."""
    list_display = ['tenant', 'feature', 'value', 'expires_at', 'created_at']
    list_filter = ['expires_at', 'created_at']
    search_fields = ['tenant__name', 'feature__key']
    readonly_fields = ['id', 'created_at', 'updated_at']
    autocomplete_fields = ['tenant', 'feature']
