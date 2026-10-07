from rest_framework import serializers
from .models import (
    ClientRetentionMetrics, RetentionCampaignActionLog, RetentionCampaignTrigger,
    RetentionConversionAttribution, SavedSegment, TenantRetentionDailySnapshot,
    WeeklyBusinessInsight
)
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
            'estimated_monthly_value',
            'at_risk_revenue',
            'is_high_value',
            'failed_payments_last_90d',
            'churn_risk_score',
            'risk_level',
            'risk_factors',
            'ai_risk_summary',
            'ai_evaluated_at',
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


class RetentionCampaignTriggerSerializer(serializers.ModelSerializer):
    target_segment_name = serializers.CharField(source='target_segment.name', read_only=True)
    total_actions_sent = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = RetentionCampaignTrigger
        fields = [
            'id',
            'name',
            'is_active',
            'trigger_type',
            'trigger_value',
            'target_segment',
            'target_segment_name',
            'channel',
            'template_subject',
            'template_body',
            'use_ai_personalization',
            'total_actions_sent',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'target_segment_name', 'total_actions_sent']


class RetentionCampaignActionLogSerializer(serializers.ModelSerializer):
    client_email = serializers.CharField(source='client.email', read_only=True)
    client_name = serializers.CharField(source='client.full_name', read_only=True)
    trigger_name = serializers.CharField(source='trigger.name', read_only=True)

    class Meta:
        model = RetentionCampaignActionLog
        fields = [
            'id',
            'client',
            'client_email',
            'client_name',
            'trigger',
            'trigger_name',
            'action_type',
            'sent_at',
            'converted_at',
            'conversion_booking',
            'ai_generated_body',
            'created_at',
        ]
        read_only_fields = fields


class RetentionConversionAttributionSerializer(serializers.ModelSerializer):
    client_email = serializers.CharField(source='client.email', read_only=True)
    client_name = serializers.CharField(source='client.full_name', read_only=True)
    trigger_name = serializers.CharField(source='action_log.trigger.name', read_only=True)

    class Meta:
        model = RetentionConversionAttribution
        fields = [
            'id',
            'action_log',
            'campaign',
            'client',
            'client_email',
            'client_name',
            'trigger_name',
            'booking',
            'payment',
            'conversion_event',
            'attributed_revenue',
            'converted_at',
            'created_at',
        ]
        read_only_fields = fields


class WeeklyBusinessInsightSerializer(serializers.ModelSerializer):
    class Meta:
        model = WeeklyBusinessInsight
        fields = [
            'id',
            'week_start',
            'week_end',
            'executive_summary',
            'revenue_insights',
            'retention_insights',
            'recommended_actions',
            'generated_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields

