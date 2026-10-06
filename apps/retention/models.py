from django.db import models
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
    last_calculated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Client Retention Metrics')
        verbose_name_plural = _('Client Retention Metrics')
        indexes = [
            models.Index(fields=['tenant', 'risk_level', 'days_since_last_visit'], name='ret_metric_tenant_risk_idx'),
            models.Index(fields=['tenant', 'churn_risk_score'], name='ret_metric_tenant_score_idx'),
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

