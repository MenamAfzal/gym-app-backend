from rest_framework import serializers
from .models import ClientRetentionMetrics, SavedSegment, TenantRetentionDailySnapshot
from apps.users.serializers import UserSerializer


class ClientRetentionMetricsSerializer(serializers.ModelSerializer):
    client_email = serializers.CharField(source='client.email', read_only=True)
    client_name = serializers.CharField(source='client.full_name', read_only=True)
    lifecycle_status = serializers.CharField(source='client.lifecycle_status', read_only=True)

    class Meta:
        model = ClientRetentionMetrics
        fields = [
            'id',
            'client',
            'client_email',
            'client_name',
            'lifecycle_status',
            'first_visit_at',
            'second_visit_at',
            'last_visit_at',
            'last_booking_at',
            'days_since_last_visit',
            'visits_last_7d',
            'visits_last_30d',
            'visits_prev_30d',
            'visits_last_90d',
            'visit_frequency_weekly_30d',
            'attendance_trend',
            'total_bookings',
            'total_attended_classes',
            'total_facility_checkins',
            'total_cancellations',
            'total_late_cancellations',
            'total_no_shows',
            'booking_to_attendance_rate',
            'cancellation_rate',
            'no_show_rate',
            'active_packages_count',
            'total_credits_remaining',
            'credit_utilization_rate',
            'nearest_package_expiry_at',
            'lifetime_value',
            'average_spend',
            'churn_risk_score',
            'risk_level',
            'risk_factors',
            'last_calculated_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class SavedSegmentSerializer(serializers.ModelSerializer):
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)

    class Meta:
        model = SavedSegment
        fields = [
            'id',
            'name',
            'description',
            'filter_criteria',
            'is_system_preset',
            'created_by',
            'created_by_email',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_by', 'created_by_email', 'created_at', 'updated_at']


class TenantRetentionDailySnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantRetentionDailySnapshot
        fields = [
            'id',
            'snapshot_date',
            'total_active_members',
            'total_leads',
            'total_trials',
            'total_at_risk',
            'total_inactive',
            'total_churned',
            'reactivated_last_30d',
            'avg_visit_frequency',
            'churn_rate_monthly',
            'retention_rate_monthly',
            'attended_today',
            'cancellations_today',
            'no_shows_today',
            'expiring_packages_next_7d',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

