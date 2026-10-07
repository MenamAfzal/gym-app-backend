import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from typing import List, Optional

from django.db import transaction
from django.db.models import Avg, Case, Count, DecimalField, F, Max, Min, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncMonth
from django.utils import timezone

from apps.core.tenants.context import bypass_tenant_isolation
from apps.core.tenants.models import Tenant
from apps.scheduling.models import (
    Booking, ClassSession, ClassTemplate, FacilityAccessLog, Package, Payment
)
from apps.users.models import ClientLifecycleStatus, User, UserRole
from .models import (
    AttendanceTrend, ChurnRiskLevel, ClientRetentionMetrics,
    RetentionActionType, RetentionCampaignActionLog, RetentionCampaignTrigger,
    RetentionConversionAttribution, TenantRetentionDailySnapshot
)

logger = logging.getLogger(__name__)


class RetentionMetricsService:
    """
    High-performance bulk recalculation engine for client retention,
    customer intelligence, and lifecycle churn metrics.
    Guarantees zero N+1 queries.
    """

    @classmethod
    def recalculate_for_tenant(
        cls,
        tenant_id: str,
        client_ids: Optional[List[str]] = None
    ) -> int:
        """
        Recalculates retention metrics for all (or specified) CLIENT users
        in a tenant using set-based SQL aggregations and bulk upserts.
        """
        now = timezone.now()
        today = now.date()

        d7_ago = now - timedelta(days=7)
        d30_ago = now - timedelta(days=30)
        d60_ago = now - timedelta(days=60)
        d90_ago = now - timedelta(days=90)

        with bypass_tenant_isolation():
            # 1. Fetch targeted CLIENT users in tenant
            user_qs = User.objects.filter(
                tenant_id=tenant_id,
                role=UserRole.CLIENT
            )
            if client_ids is not None:
                user_qs = user_qs.filter(id__in=client_ids)

            clients = list(user_qs)
            if not clients:
                logger.info(f"No clients found to recalculate for tenant {tenant_id}.")
                return 0

            target_cids = [c.id for c in clients]

            # 2. Bulk aggregate Bookings
            bookings_agg = (
                Booking.all_objects.filter(
                    tenant_id=tenant_id,
                    client_id__in=target_cids
                )
                .values('client_id')
                .annotate(
                    total_bookings=Count('id'),
                    total_attended=Count('id', filter=Q(status__in=['checked_in', 'attended'])),
                    total_cancellations=Count('id', filter=Q(status='cancelled')),
                    total_late_cancellations=Count('id', filter=Q(status='cancelled', is_late_cancel=True)),
                    total_no_shows=Count('id', filter=Q(status='no_show')),
                    last_booking_at=Max('created_at'),
                    first_attended_at=Min(
                        Coalesce('checked_in_at', 'session__start_at'),
                        filter=Q(status__in=['checked_in', 'attended'])
                    ),
                    last_attended_at=Max(
                        Coalesce('checked_in_at', 'session__start_at'),
                        filter=Q(status__in=['checked_in', 'attended'])
                    ),
                    attended_7d=Count(
                        'id',
                        filter=Q(
                            status__in=['checked_in', 'attended'],
                            session__start_at__gte=d7_ago,
                            session__start_at__lte=now
                        )
                    ),
                    attended_30d=Count(
                        'id',
                        filter=Q(
                            status__in=['checked_in', 'attended'],
                            session__start_at__gte=d30_ago,
                            session__start_at__lte=now
                        )
                    ),
                    attended_prev_30d=Count(
                        'id',
                        filter=Q(
                            status__in=['checked_in', 'attended'],
                            session__start_at__gte=d60_ago,
                            session__start_at__lt=d30_ago
                        )
                    ),
                    attended_90d=Count(
                        'id',
                        filter=Q(
                            status__in=['checked_in', 'attended'],
                            session__start_at__gte=d90_ago,
                            session__start_at__lte=now
                        )
                    ),
                )
            )
            booking_map = {row['client_id']: row for row in bookings_agg}

            # 3. Bulk aggregate FacilityAccessLogs
            facility_agg = (
                FacilityAccessLog.all_objects.filter(
                    tenant_id=tenant_id,
                    client_id__in=target_cids
                )
                .values('client_id')
                .annotate(
                    total_facility_checkins=Count('id'),
                    first_facility_at=Min('checked_in_at'),
                    last_facility_at=Max('checked_in_at'),
                    facility_7d=Count(
                        'id',
                        filter=Q(checked_in_at__gte=d7_ago, checked_in_at__lte=now)
                    ),
                    facility_30d=Count(
                        'id',
                        filter=Q(checked_in_at__gte=d30_ago, checked_in_at__lte=now)
                    ),
                    facility_prev_30d=Count(
                        'id',
                        filter=Q(checked_in_at__gte=d60_ago, checked_in_at__lt=d30_ago)
                    ),
                    facility_90d=Count(
                        'id',
                        filter=Q(checked_in_at__gte=d90_ago, checked_in_at__lte=now)
                    ),
                )
            )
            facility_map = {row['client_id']: row for row in facility_agg}

            # 4. Bulk aggregate Packages (including SQL-based 30-day normalized active package monthly value)
            monthly_val_expr = Case(
                When(
                    Q(status='active', expires_at__gt=now, credits_remaining__gt=0, package_type__billing_cycle='weekly'),
                    then=Coalesce('price', 'package_type__price', Value(Decimal('0.00'))) * Value(Decimal('4.33'))
                ),
                When(
                    Q(status='active', expires_at__gt=now, credits_remaining__gt=0, package_type__billing_cycle='yearly'),
                    then=Coalesce('price', 'package_type__price', Value(Decimal('0.00'))) / Value(Decimal('12.00'))
                ),
                When(
                    Q(status='active', expires_at__gt=now, credits_remaining__gt=0),
                    then=Coalesce('price', 'package_type__price', Value(Decimal('0.00'))) * Value(Decimal('1.00'))
                ),
                default=Value(Decimal('0.00')),
                output_field=DecimalField(max_digits=10, decimal_places=2)
            )

            packages_agg = (
                Package.all_objects.filter(
                    tenant_id=tenant_id,
                    client_id__in=target_cids
                )
                .values('client_id')
                .annotate(
                    total_packages=Count('id'),
                    active_packages_count=Count(
                        'id',
                        filter=Q(status='active', expires_at__gt=now, credits_remaining__gt=0)
                    ),
                    total_credits_remaining=Coalesce(
                        Sum('credits_remaining', filter=Q(status='active', expires_at__gt=now)),
                        0
                    ),
                    total_credits_allocated_sum=Coalesce(
                        Sum(Coalesce('total_credits_allocated', 'credits_remaining')),
                        0
                    ),
                    all_credits_remaining_sum=Coalesce(Sum('credits_remaining'), 0),
                    nearest_package_expiry_at=Min(
                        'expires_at',
                        filter=Q(status='active', expires_at__gt=now, credits_remaining__gt=0)
                    ),
                    active_package_monthly_sum=Coalesce(
                        Sum(monthly_val_expr),
                        Value(Decimal('0.00'))
                    ),
                )
            )
            package_map = {row['client_id']: row for row in packages_agg}

            # 5. Bulk aggregate Payments (completed financial totals and failed attempts in last 90d)
            payments_agg = (
                Payment.all_objects.filter(
                    tenant_id=tenant_id,
                    client_id__in=target_cids
                )
                .values('client_id')
                .annotate(
                    lifetime_value=Coalesce(Sum('amount', filter=Q(status='completed')), Value(Decimal('0.00'))),
                    payment_count=Count('id', filter=Q(status='completed')),
                    average_spend=Coalesce(Avg('amount', filter=Q(status='completed')), Value(Decimal('0.00'))),
                    failed_payments_last_90d=Count(
                        'id',
                        filter=Q(status='failed', created_at__gte=d90_ago, created_at__lte=now)
                    ),
                )
            )
            payment_map = {row['client_id']: row for row in payments_agg}

            # 5b. Tenant-wide paying clients LTV distribution (for top 20% / big spender threshold)
            if client_ids is not None:
                tenant_paying_ltvs = list(
                    Payment.all_objects.filter(
                        tenant_id=tenant_id,
                        status='completed'
                    )
                    .values('client_id')
                    .annotate(total=Sum('amount'))
                    .filter(total__gt=0)
                    .order_by('total')
                    .values_list('total', flat=True)
                )
            else:
                tenant_paying_ltvs = sorted([
                    row['lifetime_value']
                    for row in payment_map.values()
                    if row.get('lifetime_value', 0) > Decimal('0.00')
                ])

            if len(tenant_paying_ltvs) < 5:
                top_20_ltv_threshold = Decimal('500.00')
            else:
                idx = int(len(tenant_paying_ltvs) * 0.8)
                top_20_ltv_threshold = tenant_paying_ltvs[min(idx, len(tenant_paying_ltvs) - 1)]

            # 5c. Precompute estimated monthly value for target clients and tenant mean
            client_est_monthly = {}
            for user in clients:
                p_data = package_map.get(user.id, {})
                user_pay = payment_map.get(user.id, {})
                user_ltv = Decimal(str(user_pay.get('lifetime_value', '0.00')))
                user_avg = Decimal(str(user_pay.get('average_spend', '0.00')))
                active_pkgs = p_data.get('active_packages_count', 0)
                pkg_monthly = Decimal(str(p_data.get('active_package_monthly_sum', '0.00')))

                b_info = booking_map.get(user.id, {})
                f_info = facility_map.get(user.id, {})
                v_90d = b_info.get('attended_90d', 0) + f_info.get('facility_90d', 0)

                if active_pkgs > 0:
                    if pkg_monthly <= Decimal('0.00') and user_avg > Decimal('0.00'):
                        client_est_monthly[user.id] = round(user_avg, 2)
                    else:
                        client_est_monthly[user.id] = round(pkg_monthly, 2)
                else:
                    if v_90d > 0 and user_ltv > Decimal('0.00'):
                        days_joined = max(1, (today - user.date_joined.date()).days)
                        months_joined = max(1, days_joined // 30)
                        client_est_monthly[user.id] = round(user_ltv / Decimal(str(months_joined)), 2)
                    else:
                        client_est_monthly[user.id] = Decimal('0.00')

            if client_ids is not None:
                mean_res = ClientRetentionMetrics.all_objects.filter(tenant_id=tenant_id).aggregate(
                    m=Avg('estimated_monthly_value')
                )['m']
                if mean_res is not None and mean_res > 0:
                    tenant_mean_monthly = Decimal(str(mean_res))
                else:
                    tot_monthly = sum(client_est_monthly.values())
                    tenant_mean_monthly = round(tot_monthly / Decimal(str(len(clients))), 2) if clients else Decimal('0.00')
            else:
                tot_monthly = sum(client_est_monthly.values())
                tenant_mean_monthly = round(tot_monthly / Decimal(str(len(clients))), 2) if clients else Decimal('0.00')
                tenant_mean_monthly = round(tot_monthly / Decimal(str(len(clients))), 2) if clients else Decimal('0.00')

            # 6. Existing metrics lookup (preserves immutable historical data like second_visit_at)
            existing_metrics = {
                m.client_id: m
                for m in ClientRetentionMetrics.all_objects.filter(
                    tenant_id=tenant_id,
                    client_id__in=target_cids
                )
            }

            # 7. Compute second_visit_at for clients with >= 2 visits missing second_visit_at
            cids_needing_second_visit = [
                c.id for c in clients
                if (
                    c.id not in existing_metrics or
                    not existing_metrics[c.id].second_visit_at
                ) and (
                    booking_map.get(c.id, {}).get('total_attended', 0) +
                    facility_map.get(c.id, {}).get('total_facility_checkins', 0)
                ) >= 2
            ]

            second_visit_map = {}
            if cids_needing_second_visit:
                raw_bk_visits = (
                    Booking.all_objects.filter(
                        tenant_id=tenant_id,
                        client_id__in=cids_needing_second_visit,
                        status__in=['checked_in', 'attended']
                    )
                    .values('client_id', 'checked_in_at', 'session__start_at')
                )
                raw_fac_visits = (
                    FacilityAccessLog.all_objects.filter(
                        tenant_id=tenant_id,
                        client_id__in=cids_needing_second_visit
                    )
                    .values('client_id', 'checked_in_at')
                )

                client_timeline = {cid: [] for cid in cids_needing_second_visit}
                for row in raw_bk_visits:
                    ts = row['checked_in_at'] or row['session__start_at']
                    if ts:
                        client_timeline[row['client_id']].append(ts)
                for row in raw_fac_visits:
                    ts = row['checked_in_at']
                    if ts:
                        client_timeline[row['client_id']].append(ts)

                for cid, timestamps in client_timeline.items():
                    if len(timestamps) >= 2:
                        timestamps.sort()
                        second_visit_map[cid] = timestamps[1]

            # 8. Assemble metrics and lifecycle transitions
            metrics_to_upsert = []
            users_to_update = []

            for user in clients:
                b_data = booking_map.get(user.id, {})
                f_data = facility_map.get(user.id, {})
                p_data = package_map.get(user.id, {})
                pay_data = payment_map.get(user.id, {})
                existing_m = existing_metrics.get(user.id)

                # Determine first and last visit timestamps
                first_attended = b_data.get('first_attended_at')
                first_facility = f_data.get('first_facility_at')
                first_candidates = [ts for ts in (first_attended, first_facility) if ts]
                first_visit_at = min(first_candidates) if first_candidates else None

                last_attended = b_data.get('last_attended_at')
                last_facility = f_data.get('last_facility_at')
                last_candidates = [ts for ts in (last_attended, last_facility) if ts]
                last_visit_at = max(last_candidates) if last_candidates else None

                # Second visit
                second_visit_at = None
                if existing_m and existing_m.second_visit_at:
                    second_visit_at = existing_m.second_visit_at
                elif user.id in second_visit_map:
                    second_visit_at = second_visit_map[user.id]

                last_booking_at = b_data.get('last_booking_at')

                # Days since last visit
                days_since_last_visit = None
                if last_visit_at:
                    days_since_last_visit = max(0, (today - last_visit_at.date()).days)

                # Visits in windows
                visits_7d = b_data.get('attended_7d', 0) + f_data.get('facility_7d', 0)
                visits_30d = b_data.get('attended_30d', 0) + f_data.get('facility_30d', 0)
                visits_prev30 = b_data.get('attended_prev_30d', 0) + f_data.get('facility_prev_30d', 0)
                visits_90d = b_data.get('attended_90d', 0) + f_data.get('facility_90d', 0)

                visit_freq = round(Decimal(str(visits_30d)) / Decimal('4.28'), 2)

                # Attendance Trend
                if visits_30d == 0 and visits_prev30 == 0:
                    attendance_trend = AttendanceTrend.INACTIVE
                elif visits_30d > visits_prev30:
                    attendance_trend = AttendanceTrend.INCREASING
                elif visits_30d < visits_prev30:
                    attendance_trend = AttendanceTrend.DECLINING
                else:
                    attendance_trend = AttendanceTrend.STABLE

                # Lifetime Counts
                total_bookings = b_data.get('total_bookings', 0)
                total_attended_classes = b_data.get('total_attended', 0)
                total_facility_checkins = f_data.get('total_facility_checkins', 0)
                total_cancellations = b_data.get('total_cancellations', 0)
                total_late_cancellations = b_data.get('total_late_cancellations', 0)
                total_no_shows = b_data.get('total_no_shows', 0)

                # Rates
                if total_bookings > 0:
                    booking_to_attendance_rate = round(
                        Decimal(str(total_attended_classes)) / Decimal(str(total_bookings)) * Decimal('100.0'),
                        2
                    )
                    cancellation_rate = round(
                        Decimal(str(total_cancellations)) / Decimal(str(total_bookings)) * Decimal('100.0'),
                        2
                    )
                    no_show_rate = round(
                        Decimal(str(total_no_shows)) / Decimal(str(total_bookings)) * Decimal('100.0'),
                        2
                    )
                else:
                    booking_to_attendance_rate = Decimal('0.00')
                    cancellation_rate = Decimal('0.00')
                    no_show_rate = Decimal('0.00')

                # Packages & Credits
                active_pkgs = p_data.get('active_packages_count', 0)
                tot_credits_rem = p_data.get('total_credits_remaining', 0)
                tot_allocated = p_data.get('total_credits_allocated_sum', 0)
                all_rem = p_data.get('all_credits_remaining_sum', 0)
                credits_used = max(0, tot_allocated - all_rem)
                credit_utilization_rate = (
                    min(Decimal('100.00'), round(Decimal(str(credits_used)) / Decimal(str(tot_allocated)) * Decimal('100.0'), 2))
                    if tot_allocated > 0 else Decimal('0.00')
                )
                nearest_pkg_exp = p_data.get('nearest_package_expiry_at')

                # Financials
                ltv = Decimal(str(pay_data.get('lifetime_value', '0.00')))
                avg_spend = Decimal(str(pay_data.get('average_spend', '0.00')))
                failed_payments_last_90d = pay_data.get('failed_payments_last_90d', 0)

                # Churn Risk Scoring & Risk Factors
                churn_score = 0
                risk_factors = []

                if days_since_last_visit is not None:
                    if days_since_last_visit >= 60:
                        churn_score += 40
                        risk_factors.append("No visits in over 60 days")
                    elif days_since_last_visit >= 30:
                        churn_score += 30
                        risk_factors.append("No visits in the last 30 days")
                    elif days_since_last_visit >= 14:
                        churn_score += 15
                        risk_factors.append("No visits in 14-30 days")
                else:
                    churn_score += 25
                    risk_factors.append("Client has never attended a class or visited")

                if visits_prev30 > 0 and visits_30d < (visits_prev30 * 0.5):
                    churn_score += 25
                    risk_factors.append("Attendance dropped by >50% vs previous month")
                elif attendance_trend == AttendanceTrend.DECLINING:
                    churn_score += 15
                    risk_factors.append("Declining attendance trend")

                if active_pkgs == 0:
                    churn_score += 20
                    risk_factors.append("No active package or pass")
                elif nearest_pkg_exp:
                    days_to_exp = (nearest_pkg_exp.date() - today).days
                    if 0 <= days_to_exp <= 7:
                        churn_score += 10
                        risk_factors.append(f"Package expiring in {days_to_exp} days")

                if no_show_rate >= Decimal('25.0'):
                    churn_score += 15
                    risk_factors.append(f"High no-show rate ({no_show_rate}%)")

                if cancellation_rate >= Decimal('40.0'):
                    churn_score += 10
                    risk_factors.append(f"High cancellation rate ({cancellation_rate}%)")

                if failed_payments_last_90d > 0:
                    churn_score += 15
                    risk_factors.append("Recent failed payment")

                churn_risk_score = min(100, churn_score)

                if churn_risk_score >= 70:
                    risk_level = ChurnRiskLevel.CRITICAL
                elif churn_risk_score >= 45:
                    risk_level = ChurnRiskLevel.HIGH
                elif churn_risk_score >= 20:
                    risk_level = ChurnRiskLevel.MEDIUM
                else:
                    risk_level = ChurnRiskLevel.LOW

                # Lifecycle Transitions & Reactivation Detection
                has_recent_visit = (visits_7d > 0) or (
                    days_since_last_visit is not None and days_since_last_visit <= 7
                )
                prev_lifecycle = user.lifecycle_status
                new_lifecycle = prev_lifecycle
                new_reactivated_at = user.reactivated_at

                if prev_lifecycle in [ClientLifecycleStatus.INACTIVE, ClientLifecycleStatus.CHURNED] and has_recent_visit:
                    new_lifecycle = ClientLifecycleStatus.ACTIVE
                    new_reactivated_at = now
                elif days_since_last_visit is not None:
                    if days_since_last_visit >= 60 and active_pkgs == 0:
                        new_lifecycle = ClientLifecycleStatus.CHURNED
                    elif days_since_last_visit >= 30:
                        new_lifecycle = ClientLifecycleStatus.INACTIVE
                    elif days_since_last_visit >= 14 or (visits_prev30 > 0 and visits_30d < (visits_prev30 * 0.5)):
                        new_lifecycle = ClientLifecycleStatus.AT_RISK
                    else:
                        new_lifecycle = ClientLifecycleStatus.ACTIVE
                else:
                    if prev_lifecycle in [ClientLifecycleStatus.LEAD, ClientLifecycleStatus.TRIAL]:
                        new_lifecycle = prev_lifecycle
                    elif active_pkgs > 0 or total_bookings > 0:
                        new_lifecycle = ClientLifecycleStatus.ACTIVE

                if new_lifecycle != prev_lifecycle or new_reactivated_at != user.reactivated_at:
                    user.lifecycle_status = new_lifecycle
                    user.reactivated_at = new_reactivated_at
                    users_to_update.append(user)

                # Phase 2 Financial Risk & Intelligence values
                est_monthly = client_est_monthly.get(user.id, Decimal('0.00'))

                is_high_val = False
                if ltv > Decimal('0.00') and ltv >= top_20_ltv_threshold:
                    is_high_val = True
                elif tenant_mean_monthly > Decimal('0.00') and est_monthly > (Decimal('1.5') * tenant_mean_monthly):
                    is_high_val = True

                if (
                    risk_level in [ChurnRiskLevel.HIGH, ChurnRiskLevel.CRITICAL] or
                    new_lifecycle == ClientLifecycleStatus.AT_RISK or
                    user.lifecycle_status == ClientLifecycleStatus.AT_RISK
                ):
                    at_risk_revenue = est_monthly
                else:
                    at_risk_revenue = Decimal('0.00')

                ai_summary = existing_m.ai_risk_summary if existing_m else ""
                ai_evaluated_at = existing_m.ai_evaluated_at if existing_m else None

                metric_obj = ClientRetentionMetrics(
                    tenant_id=tenant_id,
                    client=user,
                    first_visit_at=first_visit_at,
                    second_visit_at=second_visit_at,
                    last_visit_at=last_visit_at,
                    last_booking_at=last_booking_at,
                    days_since_last_visit=days_since_last_visit,
                    visits_last_7d=visits_7d,
                    visits_last_30d=visits_30d,
                    visits_prev_30d=visits_prev30,
                    visits_last_90d=visits_90d,
                    visit_frequency_weekly_30d=visit_freq,
                    attendance_trend=attendance_trend,
                    total_bookings=total_bookings,
                    total_attended_classes=total_attended_classes,
                    total_facility_checkins=total_facility_checkins,
                    total_cancellations=total_cancellations,
                    total_late_cancellations=total_late_cancellations,
                    total_no_shows=total_no_shows,
                    booking_to_attendance_rate=booking_to_attendance_rate,
                    cancellation_rate=cancellation_rate,
                    no_show_rate=no_show_rate,
                    active_packages_count=active_pkgs,
                    total_credits_remaining=tot_credits_rem,
                    credit_utilization_rate=credit_utilization_rate,
                    nearest_package_expiry_at=nearest_pkg_exp,
                    lifetime_value=ltv,
                    average_spend=avg_spend,
                    estimated_monthly_value=est_monthly,
                    at_risk_revenue=at_risk_revenue,
                    is_high_value=is_high_val,
                    failed_payments_last_90d=failed_payments_last_90d,
                    churn_risk_score=churn_risk_score,
                    risk_level=risk_level,
                    risk_factors=risk_factors,
                    ai_risk_summary=ai_summary,
                    ai_evaluated_at=ai_evaluated_at,
                    last_calculated_at=now,
                )
                metrics_to_upsert.append(metric_obj)

            # 9. Perform atomic bulk updates
            with transaction.atomic():
                if users_to_update:
                    User.objects.bulk_update(
                        users_to_update,
                        ['lifecycle_status', 'reactivated_at']
                    )

                ClientRetentionMetrics.objects.bulk_create(
                    metrics_to_upsert,
                    update_conflicts=True,
                    unique_fields=['client'],
                    update_fields=[
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
                    ]
                )

            logger.info(
                f"Successfully recalculated retention metrics for {len(metrics_to_upsert)} "
                f"clients in tenant {tenant_id} ({len(users_to_update)} lifecycle transitions)."
            )
            return len(metrics_to_upsert)

    @classmethod
    def recalculate_for_client(
        cls,
        tenant_id: str,
        client_id: str
    ) -> int:
        """
        Convenience method to recalculate metrics for a single client in the tenant.
        """
        return cls.recalculate_for_tenant(tenant_id=tenant_id, client_ids=[client_id])

    @classmethod
    def capture_daily_snapshot(
        cls,
        tenant,
        snapshot_date=None
    ) -> TenantRetentionDailySnapshot:
        """
        Captures a daily aggregated historical snapshot of studio retention health for a tenant.
        Powers Momence/Mindbody period-over-period trend charts and cohort curves.
        """
        now = timezone.now()
        target_date = snapshot_date or now.date()
        tenant_id = str(tenant.id if hasattr(tenant, 'id') else tenant)
        tenant_obj = tenant if hasattr(tenant, 'id') else Tenant.all_objects.filter(id=tenant_id).first()

        d30_ago = now - timedelta(days=30)
        d7_ahead = now + timedelta(days=7)

        with bypass_tenant_isolation():
            # 1. Aggregate User lifecycle breakdown
            client_qs = User.objects.filter(
                tenant_id=tenant_id,
                role=UserRole.CLIENT
            )
            lifecycle_counts = client_qs.aggregate(
                total_leads=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.LEAD)),
                total_trials=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.TRIAL)),
                total_active=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.ACTIVE)),
                total_at_risk=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.AT_RISK)),
                total_inactive=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.INACTIVE)),
                total_churned=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.CHURNED)),
                reactivated_last_30d=Count('id', filter=Q(reactivated_at__gte=d30_ago)),
            )

            total_active = lifecycle_counts['total_active'] or 0
            total_leads = lifecycle_counts['total_leads'] or 0
            total_trials = lifecycle_counts['total_trials'] or 0
            total_at_risk = lifecycle_counts['total_at_risk'] or 0
            total_inactive = lifecycle_counts['total_inactive'] or 0
            total_churned = lifecycle_counts['total_churned'] or 0
            reactivated_30d = lifecycle_counts['reactivated_last_30d'] or 0

            # Active pool = active + at_risk + inactive + churned
            active_pool = total_active + total_at_risk + total_inactive + total_churned
            if active_pool > 0:
                retention_rate = round(Decimal(str(total_active + total_at_risk)) / Decimal(str(active_pool)) * Decimal('100.0'), 2)
                churn_rate = round(Decimal(str(total_inactive + total_churned)) / Decimal(str(active_pool)) * Decimal('100.0'), 2)
            else:
                retention_rate = Decimal('0.00')
                churn_rate = Decimal('0.00')

            # 2. Average visit frequency from ClientRetentionMetrics
            metrics_agg = ClientRetentionMetrics.all_objects.filter(
                tenant_id=tenant_id
            ).aggregate(
                avg_frequency=Coalesce(Avg('visit_frequency_weekly_30d'), Value(Decimal('0.00')))
            )
            avg_freq = round(Decimal(str(metrics_agg['avg_frequency'] or '0.00')), 2)

            # 3. Attendance, cancellation, and no-show summary for target_date
            # Attended: checked_in or attended on target_date
            attended_bk = Booking.all_objects.filter(
                tenant_id=tenant_id,
                status__in=['checked_in', 'attended']
            ).filter(
                Q(checked_in_at__date=target_date) |
                Q(checked_in_at__isnull=True, session__start_at__date=target_date)
            ).count()

            facility_today = FacilityAccessLog.all_objects.filter(
                tenant_id=tenant_id,
                checked_in_at__date=target_date
            ).count()

            attended_today = attended_bk + facility_today

            # Cancellations on target_date
            cancellations_today = Booking.all_objects.filter(
                tenant_id=tenant_id,
                status='cancelled'
            ).filter(
                Q(cancelled_at__date=target_date) |
                Q(cancelled_at__isnull=True, updated_at__date=target_date)
            ).count()

            # No-shows on target_date
            no_shows_today = Booking.all_objects.filter(
                tenant_id=tenant_id,
                status='no_show'
            ).filter(
                Q(no_show_at__date=target_date) |
                Q(no_show_at__isnull=True, session__start_at__date=target_date)
            ).count()

            # 4. Expiring packages in next 7 days
            expiring_7d = Package.all_objects.filter(
                tenant_id=tenant_id,
                status='active',
                credits_remaining__gt=0,
                expires_at__gte=now,
                expires_at__lte=d7_ahead
            ).count()

            defaults = {
                'total_active_members': total_active,
                'total_leads': total_leads,
                'total_trials': total_trials,
                'total_at_risk': total_at_risk,
                'total_inactive': total_inactive,
                'total_churned': total_churned,
                'reactivated_last_30d': reactivated_30d,
                'avg_visit_frequency': avg_freq,
                'churn_rate_monthly': churn_rate,
                'retention_rate_monthly': retention_rate,
                'attended_today': attended_today,
                'cancellations_today': cancellations_today,
                'no_shows_today': no_shows_today,
                'expiring_packages_next_7d': expiring_7d,
            }

            snapshot, _ = TenantRetentionDailySnapshot.all_objects.update_or_create(
                tenant=tenant_obj,
                snapshot_date=target_date,
                defaults=defaults
            )
            logger.info(f"Captured retention snapshot for tenant {tenant_id} on {target_date}.")
            return snapshot


class SegmentQueryService:
    """
    Dynamic Momence/Mindbody-style filter evaluation service for SavedSegments
    and on-demand client retention cohort filtering.
    """

    @classmethod
    def apply_criteria(cls, queryset, filter_criteria: dict):
        """
        Applies JSON filter_criteria rules against a ClientRetentionMetrics queryset.
        Ensures client and profile are eagerly loaded to prevent N+1 queries.
        """
        queryset = queryset.select_related('client', 'client__profile')
        if not filter_criteria or not isinstance(filter_criteria, dict):
            return queryset

        now = timezone.now()

        # 1. Lifecycle Status
        lifecycle_status = filter_criteria.get('lifecycle_status')
        if lifecycle_status:
            statuses = lifecycle_status if isinstance(lifecycle_status, list) else [lifecycle_status]
            queryset = queryset.filter(client__lifecycle_status__in=statuses)

        # 2. Churn Risk Level
        risk_level = filter_criteria.get('risk_level')
        if risk_level:
            levels = risk_level if isinstance(risk_level, list) else [risk_level]
            queryset = queryset.filter(risk_level__in=levels)

        # 3. Attendance Trend
        attendance_trend = filter_criteria.get('attendance_trend')
        if attendance_trend:
            trends = attendance_trend if isinstance(attendance_trend, list) else [attendance_trend]
            queryset = queryset.filter(attendance_trend__in=trends)

        # 4. Inactivity & Recency Days
        min_days = filter_criteria.get('min_days_since_last_visit', filter_criteria.get('days_inactive_gte'))
        if min_days is not None:
            try:
                queryset = queryset.filter(days_since_last_visit__gte=int(min_days))
            except (ValueError, TypeError):
                pass

        max_days = filter_criteria.get('max_days_since_last_visit', filter_criteria.get('days_inactive_lte'))
        if max_days is not None:
            try:
                queryset = queryset.filter(days_since_last_visit__lte=int(max_days))
            except (ValueError, TypeError):
                pass

        # 5. Visit Frequency (30-day window)
        min_visits_30d = filter_criteria.get('min_visits_last_30d')
        if min_visits_30d is not None:
            try:
                queryset = queryset.filter(visits_last_30d__gte=int(min_visits_30d))
            except (ValueError, TypeError):
                pass

        max_visits_30d = filter_criteria.get('max_visits_last_30d')
        if max_visits_30d is not None:
            try:
                queryset = queryset.filter(visits_last_30d__lte=int(max_visits_30d))
            except (ValueError, TypeError):
                pass

        # 6. Credits Remaining & Unused Balances
        min_credits = filter_criteria.get('min_credits_remaining', filter_criteria.get('unused_credits_gte'))
        if min_credits is not None:
            try:
                queryset = queryset.filter(total_credits_remaining__gte=int(min_credits))
            except (ValueError, TypeError):
                pass

        max_credits = filter_criteria.get('max_credits_remaining', filter_criteria.get('unused_credits_lte'))
        if max_credits is not None:
            try:
                queryset = queryset.filter(total_credits_remaining__lte=int(max_credits))
            except (ValueError, TypeError):
                pass

        # 7. Credit Utilization Rate (Under-utilization / Low Usage Alert)
        max_util = filter_criteria.get('max_credit_utilization_rate')
        if max_util is not None:
            try:
                queryset = queryset.filter(credit_utilization_rate__lte=Decimal(str(max_util)))
            except (ValueError, TypeError):
                pass

        min_util = filter_criteria.get('min_credit_utilization_rate')
        if min_util is not None:
            try:
                queryset = queryset.filter(credit_utilization_rate__gte=Decimal(str(min_util)))
            except (ValueError, TypeError):
                pass

        # 8. Expiring Packages Window
        expiring_days = filter_criteria.get('package_expiring_within_days', filter_criteria.get('expiring_within_days'))
        if expiring_days is not None:
            try:
                exp_delta = timedelta(days=int(expiring_days))
                queryset = queryset.filter(
                    nearest_package_expiry_at__isnull=False,
                    nearest_package_expiry_at__gte=now,
                    nearest_package_expiry_at__lte=now + exp_delta
                )
            except (ValueError, TypeError):
                pass

        # 9. Cancellation & No-Show Rates
        min_cancel = filter_criteria.get('min_cancellation_rate')
        if min_cancel is not None:
            try:
                queryset = queryset.filter(cancellation_rate__gte=Decimal(str(min_cancel)))
            except (ValueError, TypeError):
                pass

        min_noshow = filter_criteria.get('min_no_show_rate')
        if min_noshow is not None:
            try:
                queryset = queryset.filter(no_show_rate__gte=Decimal(str(min_noshow)))
            except (ValueError, TypeError):
                pass

        # 10. Tags Matching
        tags = filter_criteria.get('tags', filter_criteria.get('tag'))
        if tags:
            tag_list = tags if isinstance(tags, list) else [tags]
            tag_q = Q()
            for t in tag_list:
                tag_str = str(t).strip()
                if tag_str:
                    tag_q |= Q(client__tags__icontains=tag_str)
            if tag_q:
                queryset = queryset.filter(tag_q)

        # 11. Assigned Trainer
        trainer_id = filter_criteria.get('assigned_trainer_id', filter_criteria.get('trainer_id'))
        if trainer_id:
            queryset = queryset.filter(
                Q(client__profile__assigned_trainer_id=trainer_id) |
                Q(client__assigned_staff_relations__staff_id=trainer_id)
            ).distinct()
 
        is_high_val = filter_criteria.get('is_high_value')
        if is_high_val is not None:
            if isinstance(is_high_val, str):
                is_high_val = is_high_val.lower() in ('true', '1', 't', 'yes')
            queryset = queryset.filter(is_high_value=bool(is_high_val))
 
        min_at_risk = filter_criteria.get('min_at_risk_revenue')
        if min_at_risk is not None:
            try:
                queryset = queryset.filter(at_risk_revenue__gte=Decimal(str(min_at_risk)))
            except (ValueError, TypeError):
                pass
 
        min_ltv = filter_criteria.get('min_lifetime_value')
        if min_ltv is not None:
            try:
                queryset = queryset.filter(lifetime_value__gte=Decimal(str(min_ltv)))
            except (ValueError, TypeError):
                pass
 
        has_failed = filter_criteria.get('has_failed_payments')
        if has_failed is not None:
            if isinstance(has_failed, str):
                has_failed = has_failed.lower() in ('true', '1', 't', 'yes')
            if has_failed:
                queryset = queryset.filter(failed_payments_last_90d__gt=0)
            else:
                queryset = queryset.filter(failed_payments_last_90d=0)

        return queryset


class FunnelAnalyticsService:
    """
    Customer Journey Funnel & 1st-to-2nd Visit Conversion Analytics Service.
    Tracks 5 sequential stages from lead acquisition to active membership conversion.
    """

    @classmethod
    def get_customer_journey_funnel(
        cls,
        tenant,
        start_date=None,
        end_date=None,
        days=90
    ) -> dict:
        tenant_id = str(tenant.id if hasattr(tenant, 'id') else tenant)
        now = timezone.now()

        # Handle days parameter
        if str(days).lower() == 'all':
            s_date = None
            e_date = None
        elif start_date or end_date:
            s_date = start_date
            e_date = end_date or now
        else:
            try:
                days_int = int(days) if days is not None else 90
            except (ValueError, TypeError):
                days_int = 90
            s_date = now - timedelta(days=days_int)
            e_date = now

        with bypass_tenant_isolation():
            # 1. Stage 1: total_leads_acquired
            client_qs = User.objects.filter(tenant_id=tenant_id, role=UserRole.CLIENT)
            if s_date:
                client_qs = client_qs.filter(date_joined__gte=s_date)
            if e_date:
                client_qs = client_qs.filter(date_joined__lte=e_date)

            cohort_clients = list(client_qs)
            cohort_cids = [c.id for c in cohort_clients]
            total_leads_acquired = len(cohort_cids)

            if total_leads_acquired == 0:
                return {
                    "period": {
                        "days": days,
                        "start_date": s_date.isoformat() if s_date else None,
                        "end_date": e_date.isoformat() if e_date else None,
                    },
                    "total_leads_acquired": 0,
                    "intro_or_package_purchased": 0,
                    "first_visit_completed": 0,
                    "second_visit_completed": 0,
                    "converted_to_active_member": 0,
                    "overall_conversion_rate_percent": 0.0,
                    "stages": [
                        {"stage_index": 1, "name": "total_leads_acquired", "label": "Total Leads Acquired", "count": 0, "conversion_rate_percent": 100.0, "drop_off_count": 0},
                        {"stage_index": 2, "name": "intro_or_package_purchased", "label": "Intro / Package Purchased", "count": 0, "step_conversion_rate_percent": 0.0, "drop_off_count": 0},
                        {"stage_index": 3, "name": "first_visit_completed", "label": "First Visit Completed", "count": 0, "step_conversion_rate_percent": 0.0, "drop_off_count": 0},
                        {"stage_index": 4, "name": "second_visit_completed", "label": "Second Visit Completed", "count": 0, "step_conversion_rate_percent": 0.0, "drop_off_count": 0},
                        {"stage_index": 5, "name": "converted_to_active_member", "label": "Converted to Active Member", "count": 0, "step_conversion_rate_percent": 0.0, "drop_off_count": 0},
                    ],
                    "first_to_second_visit_conversion": {
                        "first_visit_count": 0,
                        "second_visit_count": 0,
                        "conversion_rate_percent": 0.0,
                        "avg_days_between_first_and_second_visit": 0.0,
                    }
                }

            # 2. Stage 2: intro_or_package_purchased
            pkg_cids = set(
                Package.all_objects.filter(
                    tenant_id=tenant_id,
                    client_id__in=cohort_cids
                ).values_list('client_id', flat=True)
            )
            trial_cids = set(
                c.id for c in cohort_clients
                if c.lifecycle_status == ClientLifecycleStatus.TRIAL
            )
            stage2_cids = pkg_cids | trial_cids
            intro_or_package_purchased = len(stage2_cids)

            # 3. Stage 3 & 4: first_visit_completed & second_visit_completed
            metrics_qs = ClientRetentionMetrics.all_objects.filter(
                tenant_id=tenant_id,
                client_id__in=cohort_cids
            )
            metrics_map = {m.client_id: m for m in metrics_qs}

            first_visit_cids = set()
            second_visit_cids = set()
            visit_diff_days = []

            for cid in cohort_cids:
                m = metrics_map.get(cid)
                if m and m.first_visit_at:
                    first_visit_cids.add(cid)
                if m and m.second_visit_at:
                    second_visit_cids.add(cid)
                    if m.first_visit_at and m.second_visit_at >= m.first_visit_at:
                        diff = (m.second_visit_at - m.first_visit_at).total_seconds() / 86400.0
                        visit_diff_days.append(diff)

            first_visit_completed = len(first_visit_cids)
            second_visit_completed = len(second_visit_cids)
 
            converted_cids = set(c.id for c in cohort_clients if c.converted_at is not None)
            recurring_pkg_cids = set(
                Package.all_objects.filter(
                    tenant_id=tenant_id,
                    client_id__in=cohort_cids
                ).filter(
                    Q(package_type__billing_cycle__in=['weekly', 'monthly', 'yearly']) |
                    Q(stripe_subscription_id__isnull=False) |
                    Q(price__gt=Decimal('0.00'))
                ).values_list('client_id', flat=True)
            )
            stage5_cids = converted_cids | recurring_pkg_cids
            converted_to_active_member = len(stage5_cids)
 
            conv_1_to_2 = round(intro_or_package_purchased / total_leads_acquired * 100.0, 2)
            conv_2_to_3 = round(first_visit_completed / intro_or_package_purchased * 100.0, 2) if intro_or_package_purchased > 0 else 0.0
            conv_3_to_4 = round(second_visit_completed / first_visit_completed * 100.0, 2) if first_visit_completed > 0 else 0.0
            conv_4_to_5 = round(converted_to_active_member / second_visit_completed * 100.0, 2) if second_visit_completed > 0 else 0.0
            overall_conv = round(converted_to_active_member / total_leads_acquired * 100.0, 2)

            drop_1_to_2 = max(0, total_leads_acquired - intro_or_package_purchased)
            drop_2_to_3 = max(0, intro_or_package_purchased - first_visit_completed)
            drop_3_to_4 = max(0, first_visit_completed - second_visit_completed)
            drop_4_to_5 = max(0, second_visit_completed - converted_to_active_member)

            first_to_second_conv = round(second_visit_completed / first_visit_completed * 100.0, 2) if first_visit_completed > 0 else 0.0
            avg_visit_diff = round(sum(visit_diff_days) / len(visit_diff_days), 2) if visit_diff_days else 0.0

            return {
                "period": {
                    "days": days,
                    "start_date": s_date.isoformat() if s_date else None,
                    "end_date": e_date.isoformat() if e_date else None,
                },
                "total_leads_acquired": total_leads_acquired,
                "intro_or_package_purchased": intro_or_package_purchased,
                "first_visit_completed": first_visit_completed,
                "second_visit_completed": second_visit_completed,
                "converted_to_active_member": converted_to_active_member,
                "overall_conversion_rate_percent": overall_conv,
                "stages": [
                    {
                        "stage_index": 1,
                        "name": "total_leads_acquired",
                        "label": "Total Leads Acquired",
                        "count": total_leads_acquired,
                        "conversion_rate_percent": 100.0,
                        "drop_off_count": drop_1_to_2,
                    },
                    {
                        "stage_index": 2,
                        "name": "intro_or_package_purchased",
                        "label": "Intro / Package Purchased",
                        "count": intro_or_package_purchased,
                        "step_conversion_rate_percent": conv_1_to_2,
                        "drop_off_count": drop_2_to_3,
                    },
                    {
                        "stage_index": 3,
                        "name": "first_visit_completed",
                        "label": "First Visit Completed",
                        "count": first_visit_completed,
                        "step_conversion_rate_percent": conv_2_to_3,
                        "drop_off_count": drop_3_to_4,
                    },
                    {
                        "stage_index": 4,
                        "name": "second_visit_completed",
                        "label": "Second Visit Completed",
                        "count": second_visit_completed,
                        "step_conversion_rate_percent": conv_3_to_4,
                        "drop_off_count": drop_4_to_5,
                    },
                    {
                        "stage_index": 5,
                        "name": "converted_to_active_member",
                        "label": "Converted to Active Member",
                        "count": converted_to_active_member,
                        "step_conversion_rate_percent": conv_4_to_5,
                        "drop_off_count": 0,
                    },
                ],
                "first_to_second_visit_conversion": {
                    "first_visit_count": first_visit_completed,
                    "second_visit_count": second_visit_completed,
                    "conversion_rate_percent": first_to_second_conv,
                    "avg_days_between_first_and_second_visit": avg_visit_diff,
                }
            }


class CohortAnalyticsService:
    """
    Cohort Retention Matrix Analytics Service.
    Groups clients by acquisition month and computes M0, M1, M2... retention percentages.
    """

    @classmethod
    def get_cohort_retention_matrix(cls, tenant, months: int = 6) -> dict:
        tenant_id = str(tenant.id if hasattr(tenant, 'id') else tenant)
        now = timezone.now()
        curr_year = now.year
        curr_month = now.month
 
        cohort_year_months = []
        for i in range(months - 1, -1, -1):
            y = curr_year
            m = curr_month - i
            while m <= 0:
                m += 12
                y -= 1
            cohort_year_months.append((y, m))

        start_date = datetime(cohort_year_months[0][0], cohort_year_months[0][1], 1, tzinfo=dt_timezone.utc)

        with bypass_tenant_isolation():
            clients = (
                User.objects.filter(
                    tenant_id=tenant_id,
                    role=UserRole.CLIENT,
                    date_joined__gte=start_date
                )
                .annotate(cohort_month_trunc=TruncMonth('date_joined'))
                .values('id', 'cohort_month_trunc', 'date_joined')
            )

            cohort_client_map = {}
            for row in clients:
                t = row['cohort_month_trunc']
                key = f"{t.year:04d}-{t.month:02d}"
                cohort_client_map.setdefault(key, []).append(row['id'])

            all_cohort_cids = [row['id'] for row in clients]
            client_active_months = {cid: set() for cid in all_cohort_cids}

            if all_cohort_cids:
                booking_activity = (
                    Booking.all_objects.filter(
                        tenant_id=tenant_id,
                        client_id__in=all_cohort_cids,
                        status__in=['checked_in', 'attended']
                    )
                    .annotate(activity_month=TruncMonth(Coalesce('checked_in_at', 'session__start_at')))
                    .filter(activity_month__gte=start_date)
                    .values('client_id', 'activity_month')
                    .distinct()
                )
                for b in booking_activity:
                    am = b['activity_month']
                    if am:
                        client_active_months[b['client_id']].add(f"{am.year:04d}-{am.month:02d}")

                facility_activity = (
                    FacilityAccessLog.all_objects.filter(
                        tenant_id=tenant_id,
                        client_id__in=all_cohort_cids,
                        checked_in_at__gte=start_date
                    )
                    .annotate(activity_month=TruncMonth('checked_in_at'))
                    .values('client_id', 'activity_month')
                    .distinct()
                )
                for f in facility_activity:
                    am = f['activity_month']
                    if am:
                        client_active_months[f['client_id']].add(f"{am.year:04d}-{am.month:02d}")

            cohorts = []
            for y, m in cohort_year_months:
                cohort_key = f"{y:04d}-{m:02d}"
                cids = cohort_client_map.get(cohort_key, [])
                initial_size = len(cids)

                retention_by_month = []
                max_offset = (curr_year - y) * 12 + (curr_month - m)

                for offset in range(max_offset + 1):
                    target_y = y + (m - 1 + offset) // 12
                    target_m = (m - 1 + offset) % 12 + 1
                    target_key = f"{target_y:04d}-{target_m:02d}"

                    active_count = sum(
                        1 for cid in cids
                        if target_key in client_active_months.get(cid, set())
                    )
                    rate = round(active_count / initial_size * 100.0, 2) if initial_size > 0 else 0.0
                    retention_by_month.append({
                        "month_offset": offset,
                        "active_count": active_count,
                        "retention_rate_percent": rate
                    })

                ret_30d = retention_by_month[1]["retention_rate_percent"] if len(retention_by_month) > 1 else None
                ret_60d = retention_by_month[2]["retention_rate_percent"] if len(retention_by_month) > 2 else None
                ret_90d = retention_by_month[3]["retention_rate_percent"] if len(retention_by_month) > 3 else None

                cohorts.append({
                    "cohort_month": cohort_key,
                    "initial_size": initial_size,
                    "retention_by_month": retention_by_month,
                    "retention_30d_percent": ret_30d,
                    "retention_60d_percent": ret_60d,
                    "retention_90d_percent": ret_90d,
                })

            return {
                "months_analyzed": months,
                "cohorts": cohorts
            }


class OperationalAnalyticsService:
    """
    Studio Class Utilization & Staff / Trainer Retention Analytics.
    Provides fill rates, session capacity economics, and instructor performance tracking.
    """

    @classmethod
    def get_class_utilization(cls, tenant, days: int = 30) -> dict:
        tenant_id = str(tenant.id if hasattr(tenant, 'id') else tenant)
        now = timezone.now()
        start_time = now - timedelta(days=days)

        with bypass_tenant_isolation():
            sessions = list(
                ClassSession.all_objects.filter(
                    tenant_id=tenant_id,
                    start_at__gte=start_time,
                    start_at__lte=now
                )
                .exclude(status='cancelled')
                .select_related('template')
            )

            total_sessions = len(sessions)
            total_capacity = sum(
                s.capacity or (s.template.default_capacity if s.template else 0)
                for s in sessions
            )

            session_ids = [s.id for s in sessions]
            bookings_qs = Booking.all_objects.filter(
                tenant_id=tenant_id,
                session_id__in=session_ids
            )

            template_data = {}
            for s in sessions:
                t = s.template
                if not t:
                    continue
                tid = str(t.id)
                if tid not in template_data:
                    template_data[tid] = {
                        "template_id": tid,
                        "name": t.name,
                        "sessions_count": 0,
                        "total_capacity": 0,
                        "total_booked": 0,
                        "total_attended": 0,
                        "total_cancellations": 0,
                        "total_no_shows": 0,
                        "revenue_generated": Decimal('0.00'),
                    }
                cap = s.capacity or t.default_capacity
                template_data[tid]["sessions_count"] += 1
                template_data[tid]["total_capacity"] += cap

            bk_summary = (
                bookings_qs.values('session__template_id')
                .annotate(
                    attended=Count('id', filter=Q(status__in=['checked_in', 'attended'])),
                    booked=Count('id', filter=Q(status__in=['booked', 'checked_in', 'attended', 'no_show'])),
                    cancellations=Count('id', filter=Q(status='cancelled')),
                    no_shows=Count('id', filter=Q(status='no_show')),
                )
            )

            for row in bk_summary:
                tid = str(row['session__template_id'])
                if tid in template_data:
                    template_data[tid]["total_attended"] = row['attended']
                    template_data[tid]["total_booked"] = row['booked']
                    template_data[tid]["total_cancellations"] = row['cancellations']
                    template_data[tid]["total_no_shows"] = row['no_shows']

            revenue_by_template = (
                Payment.all_objects.filter(
                    tenant_id=tenant_id,
                    related_booking__session_id__in=session_ids,
                    status='completed'
                )
                .values('related_booking__session__template_id')
                .annotate(total=Sum('amount'))
            )
            for row in revenue_by_template:
                tid = str(row['related_booking__session__template_id'])
                if tid in template_data:
                    template_data[tid]["revenue_generated"] = row['total'] or Decimal('0.00')

            template_breakdown = []
            for t_item in template_data.values():
                cap = t_item["total_capacity"]
                att = t_item["total_attended"]
                bked = t_item["total_booked"]
                ns = t_item["total_no_shows"]

                fill_pct = round(att / cap * 100.0, 2) if cap > 0 else 0.0
                ns_pct = round(ns / bked * 100.0, 2) if bked > 0 else 0.0

                template_breakdown.append({
                    "template_id": t_item["template_id"],
                    "name": t_item["name"],
                    "sessions_count": t_item["sessions_count"],
                    "total_capacity": cap,
                    "total_booked": bked,
                    "total_attended": att,
                    "fill_rate_percent": fill_pct,
                    "no_show_rate_percent": ns_pct,
                    "revenue_generated": float(t_item["revenue_generated"]),
                })

            total_attended = sum(t["total_attended"] for t in template_breakdown)
            total_booked = sum(t["total_booked"] for t in template_breakdown)
            total_cancellations = sum(t_item["total_cancellations"] for t_item in template_data.values())
            total_no_shows = sum(t_item["total_no_shows"] for t_item in template_data.values())

            overall_fill_rate = round(total_attended / total_capacity * 100.0, 2) if total_capacity > 0 else 0.0
            avg_attendees = round(total_attended / total_sessions, 2) if total_sessions > 0 else 0.0

            total_all_bk = total_booked + total_cancellations
            cancellation_rate = round(total_cancellations / total_all_bk * 100.0, 2) if total_all_bk > 0 else 0.0
            no_show_rate = round(total_no_shows / total_booked * 100.0, 2) if total_booked > 0 else 0.0

            return {
                "period_days": days,
                "total_sessions_held": total_sessions,
                "total_capacity": total_capacity,
                "total_booked": total_booked,
                "total_attended": total_attended,
                "total_cancellations": total_cancellations,
                "total_no_shows": total_no_shows,
                "studio_fill_rate_percent": overall_fill_rate,
                "avg_attendees_per_session": avg_attendees,
                "cancellation_rate_percent": cancellation_rate,
                "no_show_rate_percent": no_show_rate,
                "template_breakdown": template_breakdown,
            }

    @classmethod
    def get_staff_performance(cls, tenant, days: int = 30) -> dict:
        tenant_id = str(tenant.id if hasattr(tenant, 'id') else tenant)
        now = timezone.now()
        start_time = now - timedelta(days=days)

        with bypass_tenant_isolation():
            staff_users = list(
                User.objects.filter(
                    tenant_id=tenant_id,
                    role__in=[UserRole.TRAINER, UserRole.GYM_OWNER, UserRole.GYM_MANAGER]
                )
            )

            results = []
            for staff in staff_users:
                sessions = list(
                    ClassSession.all_objects.filter(
                        tenant_id=tenant_id,
                        staff=staff,
                        start_at__gte=start_time,
                        start_at__lte=now
                    ).exclude(status='cancelled')
                )
                classes_taught = len(sessions)
                sess_ids = [s.id for s in sessions]
                staff_cap = sum(s.capacity or (s.template.default_capacity if s.template else 0) for s in sessions)

                attended_bookings = (
                    Booking.all_objects.filter(
                        tenant_id=tenant_id,
                        session_id__in=sess_ids,
                        status__in=['checked_in', 'attended']
                    )
                )
                total_attendees = attended_bookings.count()
                avg_fill_rate = round(total_attendees / staff_cap * 100.0, 2) if staff_cap > 0 else 0.0

                assigned_clients = list(
                    User.objects.filter(
                        tenant_id=tenant_id,
                        role=UserRole.CLIENT,
                        profile__assigned_trainer=staff
                    )
                )
                assigned_count = len(assigned_clients)
                if assigned_count > 0:
                    active_assigned = sum(
                        1 for c in assigned_clients
                        if c.lifecycle_status in [ClientLifecycleStatus.ACTIVE, ClientLifecycleStatus.TRIAL]
                    )
                    assigned_retention_rate = round(active_assigned / assigned_count * 100.0, 2)
                    assigned_cids = [c.id for c in assigned_clients]
                    freq_agg = ClientRetentionMetrics.all_objects.filter(
                        tenant_id=tenant_id,
                        client_id__in=assigned_cids
                    ).aggregate(avg_freq=Avg('visit_frequency_weekly_30d'))
                    avg_freq = round(Decimal(str(freq_agg['avg_freq'] or '0.00')), 2)
                else:
                    assigned_retention_rate = 0.0
                    avg_freq = Decimal('0.00')

                client_attend_counts = (
                    attended_bookings.values('client_id')
                    .annotate(c=Count('id'))
                )
                unique_attendees = len(client_attend_counts)
                repeat_attendees = sum(1 for row in client_attend_counts if row['c'] > 1)
                repeat_rate = round(repeat_attendees / unique_attendees * 100.0, 2) if unique_attendees > 0 else 0.0

                results.append({
                    "staff_id": str(staff.id),
                    "name": staff.full_name,
                    "email": staff.email,
                    "role": staff.role,
                    "classes_taught": classes_taught,
                    "total_attendees_served": total_attendees,
                    "average_class_fill_rate_percent": avg_fill_rate,
                    "assigned_clients_count": assigned_count,
                    "assigned_clients_retention_rate_percent": assigned_retention_rate,
                    "average_visit_frequency_of_assigned_clients": float(avg_freq),
                    "repeat_visit_rate_percent": repeat_rate,
                })

            return {
                "period_days": days,
                "staff_performance": results
            }


class AttributionService:
    """
    Service for end-to-end attribution of client actions (bookings, check-ins, package purchases)
    to automated retention campaigns or marketing broadcast campaigns within an attribution window.
    """

    ATTRIBUTION_WINDOW_DAYS = 7

    @classmethod
    def attribute_conversion(
        cls,
        tenant,
        client,
        event_type: str,
        related_object=None
    ) -> Optional[RetentionConversionAttribution]:
        """
        Connects a client conversion event (booking, check-in, package payment) to a recent
        retention intervention action log or notification campaign within a 7-day window.

        Args:
            tenant: Tenant instance or UUID
            client: User instance
            event_type: 'booking_checkin', 'package_purchase', 'booking', etc.
            related_object: Booking or Payment model instance
        """
        now = timezone.now()
        window_start = now - timedelta(days=cls.ATTRIBUTION_WINDOW_DAYS)

        tenant_id = getattr(tenant, 'id', tenant)

        with bypass_tenant_isolation():
            # 1. Check for a recent RetentionCampaignActionLog (action_type='sent')
            recent_log = RetentionCampaignActionLog.all_objects.filter(
                tenant_id=tenant_id,
                client=client,
                action_type=RetentionActionType.SENT,
                sent_at__gte=window_start
            ).order_by('-sent_at').first()

            # 2. Check for a recent NotificationCampaign if no trigger log was found
            recent_campaign = None
            if not recent_log:
                try:
                    from apps.notifications.models import NotificationInbox
                    recent_inbox = NotificationInbox.all_objects.filter(
                        tenant_id=tenant_id,
                        recipient=client,
                        campaign__isnull=False,
                        created_at__gte=window_start
                    ).select_related('campaign').order_by('-created_at').first()
                    if recent_inbox:
                        recent_campaign = recent_inbox.campaign
                except Exception as e:
                    logger.warning(f"Error querying notification inbox for attribution: {e}")

            # If no recent retention intervention was sent, no attribution is made
            if not recent_log and not recent_campaign:
                return None

            # Determine booking, payment, and attributed revenue
            booking_obj = None
            payment_obj = None
            attributed_revenue = Decimal('0.00')

            if isinstance(related_object, Booking):
                booking_obj = related_object
                pkg = getattr(booking_obj, 'credit_source', None) or getattr(booking_obj, 'package', None)
                if pkg and getattr(pkg, 'price', None):
                    attributed_revenue = Decimal(str(pkg.price))
                elif hasattr(booking_obj, 'session') and booking_obj.session and hasattr(booking_obj.session, 'template') and booking_obj.session.template:
                    price = getattr(booking_obj.session.template, 'price', None)
                    if price:
                        attributed_revenue = Decimal(str(price))
            elif isinstance(related_object, Payment):
                payment_obj = related_object
                if payment_obj.amount:
                    attributed_revenue = Decimal(str(payment_obj.amount))

            # Mark the action log as converted
            if recent_log:
                recent_log.converted_at = now
                if booking_obj:
                    recent_log.conversion_booking = booking_obj
                recent_log.save(update_fields=['converted_at', 'conversion_booking'])

            # Create the attribution record
            attribution = RetentionConversionAttribution.objects.create(
                tenant_id=tenant_id,
                action_log=recent_log,
                campaign=recent_campaign,
                client=client,
                booking=booking_obj,
                payment=payment_obj,
                conversion_event=event_type,
                attributed_revenue=attributed_revenue,
                converted_at=now,
            )

            logger.info(
                f"Attributed conversion ({event_type}) for client {client.id} in tenant {tenant_id}: "
                f"ActionLog={recent_log.id if recent_log else 'None'}, "
                f"Revenue=${attributed_revenue}"
            )
            return attribution



