from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from core_models.base_models import TenantAwareModel


class AttendanceTrend(models.TextChoices):
    INCREASING = 'increasing', _('Increasing')
    STABLE = 'stable', _('Stable')
    DECLINING = 'declining', _('Declining')
    INACTIVE = 'inactive', _('Inactive')


class ChurnRiskLevel(models.TextChoices):
    LOW = 'low', _('Low')
    MEDIUM = 'medium', _('Medium')
    HIGH = 'high', _('High')
    CRITICAL = 'critical', _('Critical')


class ClientRetentionMetrics(TenantAwareModel):
    """
    Consolidated behavioral, attendance, financial, and retention metrics
    for a client within a tenant studio. Recalculated by the bulk metrics engine.
    """
    client = models.OneToOneField(
        'users.User',
        on_delete=models.CASCADE,
        related_name='retention_metrics'
    )

    # Visit & Attendance Timestamps
    first_visit_at = models.DateTimeField(null=True, blank=True)
    second_visit_at = models.DateTimeField(null=True, blank=True)
    last_visit_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Max of attended/checked-in Booking and FacilityAccessLog.checked_in_at"
    )
    last_booking_at = models.DateTimeField(null=True, blank=True)
    days_since_last_visit = models.PositiveIntegerField(null=True, blank=True)

    # Recency & Frequency Windows
    visits_last_7d = models.PositiveIntegerField(default=0)
    visits_last_30d = models.PositiveIntegerField(default=0)
    visits_prev_30d = models.PositiveIntegerField(
        default=0,
        help_text="Visits between day -60 and day -31 for trend/velocity comparison"
    )
    visits_last_90d = models.PositiveIntegerField(default=0)
    visit_frequency_weekly_30d = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        help_text="visits_last_30d / 4.28"
    )
    attendance_trend = models.CharField(
        max_length=20,
        choices=AttendanceTrend.choices,
        default=AttendanceTrend.STABLE
    )

    # Lifetime Attendance & Reliability Counts
    total_bookings = models.PositiveIntegerField(default=0)
    total_attended_classes = models.PositiveIntegerField(default=0)
    total_facility_checkins = models.PositiveIntegerField(default=0)
    total_cancellations = models.PositiveIntegerField(default=0)
    total_late_cancellations = models.PositiveIntegerField(default=0)
    total_no_shows = models.PositiveIntegerField(default=0)

    # Rates (Percentages 0.00 to 100.00)
    booking_to_attendance_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00
    )
    cancellation_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00
    )
    no_show_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00
    )

    # Package & Credit Utilization
    active_packages_count = models.PositiveIntegerField(default=0)
    total_credits_remaining = models.PositiveIntegerField(default=0)
    credit_utilization_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00
    )
    nearest_package_expiry_at = models.DateTimeField(null=True, blank=True)

    # Financial Metrics
    lifetime_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0.00
    )
    average_spend = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00
    )

    # Churn Risk & Health Scoring
    churn_risk_score = models.PositiveIntegerField(default=0)
    risk_level = models.CharField(
        max_length=20,
        choices=ChurnRiskLevel.choices,
        default=ChurnRiskLevel.LOW
    )
    risk_factors = models.JSONField(default=list, blank=True)
 
    estimated_monthly_value = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Projected monthly recurring or package value for this client"
    )
    at_risk_revenue = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Revenue at stake if this client is HIGH or CRITICAL risk"
    )
    is_high_value = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Big Spender / High-Value customer flag"
    )
    failed_payments_last_90d = models.PositiveIntegerField(
        default=0,
        help_text="Count of failed payment attempts in the last 90 days"
    )
    ai_risk_summary = models.TextField(
        blank=True,
        default="",
        help_text="AI-generated plain-language explanation of churn risk and recommended staff action"
    )
    ai_evaluated_at = models.DateTimeField(null=True, blank=True)

    last_calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Client Retention Metrics')
        verbose_name_plural = _('Client Retention Metrics')
        indexes = [
            models.Index(fields=['tenant', 'risk_level', 'days_since_last_visit'], name='ret_metric_tenant_risk_idx'),
            models.Index(fields=['tenant', 'churn_risk_score'], name='ret_metric_tenant_score_idx'),
            models.Index(fields=['tenant', 'is_high_value'], name='ret_metric_high_val_idx'),
        ]

    def __str__(self):
        return f"RetentionMetrics for {self.client_id} (Tenant: {self.tenant_id}, Risk: {self.risk_level})"


class SavedSegment(TenantAwareModel):
    """
    Dynamic Momence/Mindbody-style saved customer segments and cohorts.
    """
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    filter_criteria = models.JSONField(
        default=dict,
        help_text="JSON filter rules: lifecycle_status, days_inactive_gte, tags, risk_level, expiring_within_days, unused_credits_gte, assigned_trainer_id, etc."
    )
    is_system_preset = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_retention_segments'
    )

    class Meta:
        verbose_name = _('Saved Segment')
        verbose_name_plural = _('Saved Segments')
        ordering = ['-is_system_preset', 'name']
        indexes = [
            models.Index(fields=['tenant', 'is_system_preset'], name='ret_seg_tenant_preset_idx'),
        ]

    def __str__(self):
        return f"{self.name} ({'System' if self.is_system_preset else 'Custom'})"


class TenantRetentionDailySnapshot(TenantAwareModel):
    """
    Daily aggregated historical snapshot of studio retention health.
    Powers Momence/Mindbody period-over-period trend charts and cohort curves.
    """
    snapshot_date = models.DateField(db_index=True)

    total_active_members = models.PositiveIntegerField(default=0)
    total_leads = models.PositiveIntegerField(default=0)
    total_trials = models.PositiveIntegerField(default=0)
    total_at_risk = models.PositiveIntegerField(default=0)
    total_inactive = models.PositiveIntegerField(default=0)
    total_churned = models.PositiveIntegerField(default=0)
    reactivated_last_30d = models.PositiveIntegerField(default=0)

    # 30-Day Health
    avg_visit_frequency = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    churn_rate_monthly = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    retention_rate_monthly = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)

    # Attendance summary for the date
    attended_today = models.PositiveIntegerField(default=0)
    cancellations_today = models.PositiveIntegerField(default=0)
    no_shows_today = models.PositiveIntegerField(default=0)
    expiring_packages_next_7d = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _('Tenant Retention Daily Snapshot')
        verbose_name_plural = _('Tenant Retention Daily Snapshots')
        ordering = ['-snapshot_date']
        unique_together = ['tenant', 'snapshot_date']
        indexes = [
            models.Index(fields=['tenant', 'snapshot_date'], name='ret_snap_tenant_date_idx'),
        ]

    def __str__(self):
        return f"{self.tenant.name} Snapshot - {self.snapshot_date}"


class RetentionTriggerType(models.TextChoices):
    INACTIVITY = 'inactivity', _('Inactivity')
    FAILED_PAYMENT = 'failed_payment', _('Failed Payment')
    PACKAGE_EXPIRY = 'package_expiry', _('Package Expiry')
    CUSTOM_SEGMENT = 'custom_segment', _('Custom Segment')


class RetentionChannel(models.TextChoices):
    EMAIL = 'email', _('Email')
    PUSH = 'push', _('Push')
    SMS = 'sms', _('SMS')


class RetentionActionType(models.TextChoices):
    SENT = 'sent', _('Sent')
    CLICKED = 'clicked', _('Clicked')
    CONVERTED = 'converted', _('Converted')


class RetentionCampaignTrigger(TenantAwareModel):
    """
    Automated retention intervention trigger rules (e.g. 14 days inactive, failed billing, pass expiry).
    """
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True, db_index=True)
    trigger_type = models.CharField(
        max_length=30,
        choices=RetentionTriggerType.choices,
        default=RetentionTriggerType.INACTIVITY
    )
    trigger_value = models.IntegerField(
        default=0,
        help_text="Milestone or offset value (e.g. 14 for days inactive, or 7 for days before expiry)"
    )
    target_segment = models.ForeignKey(
        'retention.SavedSegment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='triggers'
    )
    channel = models.CharField(
        max_length=20,
        choices=RetentionChannel.choices,
        default=RetentionChannel.EMAIL
    )
    template_subject = models.CharField(max_length=255, blank=True, default='')
    template_body = models.TextField()
    use_ai_personalization = models.BooleanField(
        default=False,
        help_text="Whether to generate LLM-synthesized winback copy tailored to individual risk factors"
    )

    class Meta:
        verbose_name = _('Retention Campaign Trigger')
        verbose_name_plural = _('Retention Campaign Triggers')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'is_active', 'trigger_type'], name='ret_trig_tenant_active_idx'),
        ]

    def __str__(self):
        return f"{self.name} ({self.trigger_type} - {self.channel})"


class RetentionCampaignActionLog(TenantAwareModel):
    """
    Audit log of dispatched automated retention touchpoints and client response lifecycle.
    """
    client = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='retention_action_logs'
    )
    trigger = models.ForeignKey(
        'retention.RetentionCampaignTrigger',
        on_delete=models.CASCADE,
        related_name='action_logs'
    )
    action_type = models.CharField(
        max_length=20,
        choices=RetentionActionType.choices,
        default=RetentionActionType.SENT
    )
    sent_at = models.DateTimeField(default=timezone.now, db_index=True)
    converted_at = models.DateTimeField(null=True, blank=True)
    conversion_booking = models.ForeignKey(
        'scheduling.Booking',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='retention_action_logs'
    )
    ai_generated_body = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = _('Retention Campaign Action Log')
        verbose_name_plural = _('Retention Campaign Action Logs')
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['tenant', 'client', 'trigger', 'action_type'], name='ret_act_cl_trig_idx'),
            models.Index(fields=['tenant', 'sent_at'], name='ret_act_sent_idx'),
        ]

    def __str__(self):
        return f"ActionLog {self.action_type} for Client {self.client_id} (Trigger: {self.trigger.name})"


class RetentionConversionAttribution(TenantAwareModel):
    """
    Attribution tracking record connecting retention campaigns or automated action logs
    to concrete revenue or attendance actions (class check-ins, package purchases).
    """
    action_log = models.ForeignKey(
        'retention.RetentionCampaignActionLog',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='attributions'
    )
    campaign = models.ForeignKey(
        'notifications.NotificationCampaign',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='retention_attributions'
    )
    client = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='retention_attributions'
    )
    booking = models.ForeignKey(
        'scheduling.Booking',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='retention_attributions'
    )
    payment = models.ForeignKey(
        'scheduling.Payment',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='retention_attributions'
    )
    conversion_event = models.CharField(max_length=50, default='booking_checkin')
    attributed_revenue = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    converted_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = _('Retention Conversion Attribution')
        verbose_name_plural = _('Retention Conversion Attributions')
        ordering = ['-converted_at']
        indexes = [
            models.Index(fields=['tenant', 'client', 'converted_at'], name='ret_attr_cl_conv_idx'),
            models.Index(fields=['tenant', 'action_log'], name='ret_attr_act_log_idx'),
        ]

    def __str__(self):
        return f"Attribution for Client {self.client_id} ({self.conversion_event} - ${self.attributed_revenue})"


class WeeklyBusinessInsight(TenantAwareModel):
    """
    Weekly executive AI synthesis consolidating studio performance, retention deltas,
    revenue trends, at-risk client movements, and actionable operational recommendations.
    """
    week_start = models.DateField(db_index=True)
    week_end = models.DateField()
    executive_summary = models.TextField(help_text="Plain-language LLM executive summary of weekly performance")
    revenue_insights = models.JSONField(
        default=dict,
        blank=True,
        help_text="AI notes on MRR, package spend, and attributed conversion revenue"
    )
    retention_insights = models.JSONField(
        default=dict,
        blank=True,
        help_text="AI notes on churn rate, attendance deltas, and at-risk cohort transitions"
    )
    recommended_actions = models.JSONField(
        default=list,
        blank=True,
        help_text="Prioritized operational and marketing interventions for studio staff"
    )
    generated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _('Weekly Business Insight')
        verbose_name_plural = _('Weekly Business Insights')
        ordering = ['-week_start']
        unique_together = ['tenant', 'week_start']
        indexes = [
            models.Index(fields=['tenant', '-week_start'], name='ret_insight_tenant_week_idx'),
        ]

    def __str__(self):
        return f"{self.tenant.name} Executive Insight: {self.week_start} to {self.week_end}"

