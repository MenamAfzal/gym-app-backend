from django.contrib import admin
from .models import (
    PlatformSettings,
    TenantSubscription,
    FeatureToggle,
    PlatformLedger,
    TenantPayout,
    BillingFeature,
    BillingPlan,
    TenantBillingSubscription,
)


class TenantAdminMixin:
    def get_queryset(self, request):
        if request.user.is_superuser:
            return self.model.all_objects.all()
        return super().get_queryset(request)


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = ('id', 'platform_fee_percentage', 'created_at', 'updated_at')


@admin.register(TenantSubscription)
class TenantSubscriptionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('tenant', 'plan_name', 'stripe_subscription_id', 'status', 'current_period_end')
    list_filter = ('status', 'plan_name', 'tenant')
    search_fields = ('stripe_subscription_id', 'tenant__name')


@admin.register(FeatureToggle)
class FeatureToggleAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('tenant', 'feature_name', 'is_enabled', 'stripe_price_id')
    list_filter = ('is_enabled', 'tenant')
    search_fields = ('feature_name', 'tenant__name')


@admin.register(PlatformLedger)
class PlatformLedgerAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('transaction_id', 'tenant', 'amount_gross', 'platform_fee', 'amount_net', 'type', 'status', 'created_at')
    list_filter = ('type', 'status', 'tenant')
    search_fields = ('transaction_id', 'tenant__name', 'description')


@admin.register(TenantPayout)
class TenantPayoutAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = ('id', 'tenant', 'amount', 'currency', 'status', 'stripe_payout_id', 'created_at')
    list_filter = ('status', 'tenant')
    search_fields = ('stripe_payout_id', 'tenant__name')


@admin.register(BillingFeature)
class BillingFeatureAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'stripe_price_id', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'code', 'stripe_price_id')
    readonly_fields = ('id', 'created_at', 'updated_at')
    fieldsets = (
        ('Feature Details', {
            'fields': ('name', 'code', 'description', 'is_active'),
        }),
        ('Stripe Configuration', {
            'fields': ('stripe_price_id',),
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


@admin.register(BillingPlan)
class BillingPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'allowed_feature_count', 'is_public', 'created_at')
    list_filter = ('slug', 'is_public')
    search_fields = ('name', 'slug')
    readonly_fields = ('id', 'created_at', 'updated_at')
    fieldsets = (
        ('Plan Details', {
            'fields': ('name', 'slug', 'is_public'),
        }),
        ('Feature Constraints', {
            'fields': ('allowed_feature_count',),
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


class ActiveFeaturesInline(admin.TabularInline):
    model = TenantBillingSubscription.active_features.through
    extra = 0
    verbose_name = 'Unlocked Feature'
    verbose_name_plural = 'Unlocked Features'


@admin.register(TenantBillingSubscription)
class TenantBillingSubscriptionAdmin(TenantAdminMixin, admin.ModelAdmin):
    list_display = (
        'tenant',
        'billing_plan',
        'status',
        'stripe_subscription_id',
        'current_period_end',
        'created_at',
    )
    list_filter = ('status', 'billing_plan', 'tenant')
    search_fields = ('stripe_subscription_id', 'stripe_checkout_session_id', 'tenant__name')
    readonly_fields = (
        'id',
        'stripe_checkout_session_id',
        'stripe_subscription_id',
        'created_at',
        'updated_at',
    )
    inlines = [ActiveFeaturesInline]
    filter_horizontal = ('active_features',)
    fieldsets = (
        ('Subscription Details', {
            'fields': ('tenant', 'billing_plan', 'status', 'active_features', 'current_period_end'),
        }),
        ('Stripe IDs', {
            'fields': ('stripe_subscription_id', 'stripe_checkout_session_id'),
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )



