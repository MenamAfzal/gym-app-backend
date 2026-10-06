from django.contrib import admin
from .models import ClientRetentionMetrics, SavedSegment, TenantRetentionDailySnapshot


@admin.register(ClientRetentionMetrics)
class ClientRetentionMetricsAdmin(admin.ModelAdmin):
    list_display = ('client', 'tenant', 'risk_level', 'churn_risk_score', 'days_since_last_visit', 'visits_last_30d', 'attendance_trend', 'last_calculated_at')
    list_filter = ('tenant', 'risk_level', 'attendance_trend')
    search_fields = ('client__email', 'client__first_name', 'client__last_name')
    readonly_fields = ('last_calculated_at',)


@admin.register(SavedSegment)
class SavedSegmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'is_system_preset', 'created_by', 'created_at')
    list_filter = ('tenant', 'is_system_preset')
    search_fields = ('name', 'description')


@admin.register(TenantRetentionDailySnapshot)
class TenantRetentionDailySnapshotAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'snapshot_date', 'total_active_members', 'retention_rate_monthly', 'churn_rate_monthly', 'attended_today')
    list_filter = ('tenant', 'snapshot_date')
    readonly_fields = ('created_at', 'updated_at')

