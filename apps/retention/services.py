import logging
from datetime import timedelta
from decimal import Decimal
from typing import List, Optional

from django.db import transaction
from django.db.models import Avg, Count, Max, Min, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.core.tenants.context import bypass_tenant_isolation
from apps.scheduling.models import Booking, FacilityAccessLog, Package, Payment
from apps.users.models import ClientLifecycleStatus, User, UserRole
from .models import AttendanceTrend, ChurnRiskLevel, ClientRetentionMetrics

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

            # 4. Bulk aggregate Packages
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
                )
            )
            package_map = {row['client_id']: row for row in packages_agg}

            # 5. Bulk aggregate Payments
            payments_agg = (
                Payment.all_objects.filter(
                    tenant_id=tenant_id,
                    client_id__in=target_cids,
                    status='completed'
                )
                .values('client_id')
                .annotate(
                    lifetime_value=Coalesce(Sum('amount'), Value(Decimal('0.00'))),
                    payment_count=Count('id'),
                    average_spend=Coalesce(Avg('amount'), Value(Decimal('0.00'))),
                )
            )
            payment_map = {row['client_id']: row for row in payments_agg}

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
                    churn_risk_score=churn_risk_score,
                    risk_level=risk_level,
                    risk_factors=risk_factors,
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
                        'churn_risk_score',
                        'risk_level',
                        'risk_factors',
                        'last_calculated_at',
                    ]
                )

            logger.info(
                f"Successfully recalculated retention metrics for {len(metrics_to_upsert)} "
                f"clients in tenant {tenant_id} ({len(users_to_update)} lifecycle transitions)."
            )
            return len(metrics_to_upsert)
