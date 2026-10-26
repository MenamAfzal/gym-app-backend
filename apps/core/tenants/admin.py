"""
Tenant Admin Configuration
"""
from django.contrib import admin
from .models import (
    Tenant, Plan, Feature, PlanEntitlement,
    TenantSubscription, TenantEntitlementOverride, ReferralReward,
    AffiliatePartner, AffiliatePointsTransaction
)


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    """Admin for Tenant model."""
    list_display = [
        'name', 'subdomain', 'is_active', 'referred_by', 'affiliate',
        'stripe_customer_id', 'created_at'
    ]
    list_filter = ['is_active', 'created_at']
    search_fields = [
        'name', 'subdomain', 'referred_by__name', 'affiliate__code',
        'affiliate__company_name', 'stripe_customer_id'
    ]
    readonly_fields = ['id', 'created_at', 'updated_at']
    autocomplete_fields = ['referred_by', 'affiliate']
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'subdomain', 'is_active')
        }),
        ('Referral & Affiliate Attribution', {
            'fields': ('referred_by', 'affiliate')
        }),
        ('Stripe Billing', {
            'fields': ('stripe_customer_id', 'stripe_account_id')
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


@admin.register(AffiliatePartner)
class AffiliatePartnerAdmin(admin.ModelAdmin):
    """Admin for external B2B Affiliate Partners."""
    list_display = [
        'code', 'user', 'company_name', 'points_balance',
        'lifetime_points_earned', 'is_active', 'created_at'
    ]
    list_filter = ['is_active', 'created_at']
    search_fields = [
        'code', 'company_name', 'user__email', 'user__first_name', 'user__last_name'
    ]
    readonly_fields = ['id', 'points_balance', 'lifetime_points_earned', 'created_at', 'updated_at']
    autocomplete_fields = ['user']
    fieldsets = (
        ('Partner Identity', {
            'fields': ('user', 'code', 'company_name', 'is_active')
        }),
        ('Points & Rewards', {
            'fields': ('points_balance', 'lifetime_points_earned')
        }),
        ('Promotional Details', {
            'fields': ('website_or_social', 'metadata'),
            'classes': ('collapse',)
        }),
        ('System Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(AffiliatePointsTransaction)
class AffiliatePointsTransactionAdmin(admin.ModelAdmin):
    """Admin audit ledger for affiliate point grants, renewals, and adjustments."""
    list_display = [
        'affiliate', 'tenant', 'points', 'transaction_type', 'subscription', 'created_at'
    ]
    list_filter = ['transaction_type', 'created_at']
    search_fields = [
        'affiliate__code', 'affiliate__company_name', 'tenant__name', 'description'
    ]
    readonly_fields = ['id', 'created_at', 'updated_at']
    autocomplete_fields = ['affiliate', 'tenant', 'subscription']
    date_hierarchy = 'created_at'


@admin.register(ReferralReward)
class ReferralRewardAdmin(admin.ModelAdmin):
    """Admin for gym-to-gym referral cash rewards."""
    list_display = [
        'referrer', 'referred_tenant', 'reward_amount', 'status', 'subscription', 'billing_subscription', 'created_at'
    ]
    list_filter = ['status', 'created_at']
    search_fields = ['referrer__name', 'referred_tenant__name']
    readonly_fields = ['id', 'created_at', 'updated_at']
    autocomplete_fields = ['referrer', 'referred_tenant', 'subscription', 'billing_subscription']
    date_hierarchy = 'created_at'


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
