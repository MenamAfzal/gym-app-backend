from django.contrib import admin
from .models import ClientRetentionMetrics, SavedSegment


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
