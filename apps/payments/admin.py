from django.contrib import admin
from .models import PlatformSettings, TenantSubscription, FeatureToggle, PlatformLedger, TenantPayout

class TenantAdminMixin:
    """
    Mixin to allow Superusers to see all records across tenants in Django Admin.
    """
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
