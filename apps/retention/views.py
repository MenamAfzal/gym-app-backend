import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.tenants.context import bypass_tenant_isolation
from apps.notifications.models import NotificationInbox
from apps.scheduling.models import Booking, FacilityAccessLog, Package, Payment
from apps.scheduling.permissions import IsOwnerOrManager
from apps.scheduling.views import StandardResultsSetPagination
from apps.users.models import ClientLifecycleStatus, User, UserRole
from .models import (
    AttendanceTrend, ChurnRiskLevel, ClientRetentionMetrics,
    RetentionActionType, RetentionCampaignActionLog, RetentionCampaignTrigger,
    RetentionConversionAttribution, SavedSegment, TenantRetentionDailySnapshot
)
from .serializers import (
    ClientRetentionMetricsSerializer,
    RetentionCampaignActionLogSerializer,
    RetentionCampaignTriggerSerializer,
    RetentionConversionAttributionSerializer,
    SavedSegmentSerializer,
    TenantRetentionDailySnapshotSerializer,
)
from .services import (
    AttributionService, CohortAnalyticsService, FunnelAnalyticsService,
    OperationalAnalyticsService, RetentionMetricsService, SegmentQueryService
)
from .ai_service import RetentionAIService
from .automation_service import RetentionAutomationService

logger = logging.getLogger(__name__)


class ClientRetentionMetricsViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing and recalculating client retention metrics.
    Supports filtering by risk level, trend, expiring packages, unused credits,
    failed payments, and assigned trainer.
    """
    queryset = ClientRetentionMetrics.objects.all().select_related('client', 'client__profile', 'tenant')
    serializer_class = ClientRetentionMetricsSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    permission_app = 'retention'
    permission_resource = 'metrics'
    filterset_fields = ['risk_level', 'attendance_trend']
    search_fields = ['client__email', 'client__first_name', 'client__last_name']
    ordering_fields = ['churn_risk_score', 'days_since_last_visit', 'lifetime_value', 'visits_last_30d']

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return ClientRetentionMetrics.objects.none()

        qs = ClientRetentionMetrics.objects.select_related('client', 'client__profile', 'tenant')
        client_id = self.request.query_params.get('client_id')
        if client_id:
            qs = qs.filter(client_id=client_id)

        # Wire query params through SegmentQueryService for comprehensive Phase 1 & 2 filtering
        criteria = {}
        for key in [
            'lifecycle_status', 'risk_level', 'attendance_trend',
            'min_days_since_last_visit', 'max_days_since_last_visit',
            'days_inactive_gte', 'days_inactive_lte',
            'min_visits_last_30d', 'max_visits_last_30d',
            'min_credits_remaining', 'max_credits_remaining',
            'unused_credits_gte', 'unused_credits_lte',
            'min_credit_utilization_rate', 'max_credit_utilization_rate',
            'package_expiring_within_days', 'expiring_within_days',
            'min_cancellation_rate', 'min_no_show_rate',
            'tags', 'tag', 'assigned_trainer_id', 'trainer_id',
            'is_high_value', 'min_at_risk_revenue', 'min_lifetime_value',
            'has_failed_payments'
        ]:
            val = self.request.query_params.get(key)
            if val is not None:
                criteria[key] = val

        if criteria:
            qs = SegmentQueryService.apply_criteria(qs, criteria)

        if not qs.query.order_by:
            qs = qs.order_by('-churn_risk_score', 'id')
        return qs

    @action(detail=False, methods=['post'], url_path='recalculate')
    def recalculate(self, request):
        """
        Manually trigger recalculation of retention metrics for the current tenant or specific client.
        """
        tenant_id = getattr(request, 'tenant_id', None) or getattr(request.user, 'tenant_id', None)
        if not tenant_id:
            return Response(
                {"detail": "No tenant associated with the current request."},
                status=status.HTTP_400_BAD_REQUEST
            )

        client_id = request.data.get('client_id')
        client_ids = [client_id] if client_id else None

        processed = RetentionMetricsService.recalculate_for_tenant(
            tenant_id=str(tenant_id),
            client_ids=client_ids
        )
        return Response({
            "status": "success",
            "message": f"Successfully recalculated retention metrics for {processed} clients.",
            "processed_count": processed
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='analyze-risk')
    def analyze_risk(self, request, pk=None):
        """
        Evaluates or refreshes the AI churn risk summary and staff action recommendation
        for a specific client metrics record.
        """
        instance = self.get_object()
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        if tenant and instance.tenant_id != tenant.id:
            return Response(
                {"detail": "Forbidden across tenant boundary."},
                status=status.HTTP_403_FORBIDDEN
            )

        insight = RetentionAIService.generate_client_risk_insight(
            tenant=tenant or instance.tenant,
            metrics=instance
        )
        return Response(insight, status=status.HTTP_200_OK)


class SavedSegmentViewSet(viewsets.ModelViewSet):
    """
    CRUD API for Momence-style Saved Customer Segments and dynamic cohorts.
    """
    queryset = SavedSegment.objects.all().select_related('created_by', 'tenant')
    serializer_class = SavedSegmentSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    permission_app = 'retention'
    permission_resource = 'segments'

    def get_queryset(self):
        return SavedSegment.objects.select_related('created_by', 'tenant')

    def perform_create(self, serializer):
        tenant = getattr(self.request, 'tenant', None) or getattr(self.request.user, 'tenant', None)
        serializer.save(
            tenant=tenant,
            created_by=self.request.user
        )

    @action(detail=True, methods=['get'], url_path='members')
    def members(self, request, pk=None):
        """
        Evaluates the saved segment's criteria and returns a paginated cohort member list.
        """
        segment = self.get_object()
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        base_qs = ClientRetentionMetrics.objects.filter(tenant=tenant).order_by('-churn_risk_score', 'id')
        filtered_qs = SegmentQueryService.apply_criteria(base_qs, segment.filter_criteria)

        page = self.paginate_queryset(filtered_qs)
        if page is not None:
            serializer = ClientRetentionMetricsSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = ClientRetentionMetricsSerializer(filtered_qs, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='preview')
    def preview(self, request):
        """
        Accepts arbitrary filter_criteria in POST body and returns matching count
        and top preview records so staff can check cohort size prior to saving.
        """
        criteria = request.data.get('filter_criteria', {})
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        base_qs = ClientRetentionMetrics.objects.filter(tenant=tenant)
        filtered_qs = SegmentQueryService.apply_criteria(base_qs, criteria)

        count = filtered_qs.count()
        sample = filtered_qs[:50]
        serializer = ClientRetentionMetricsSerializer(sample, many=True)

        return Response({
            "matching_count": count,
            "results": serializer.data
        }, status=status.HTTP_200_OK)


class ClientActivityTimelineView(APIView):
    """
    Unified Customer Activity Timeline API.
    Streams normalized, chronological events for a specific client across:
    - Bookings (created, cancelled, no-show)
    - Check-ins (class check-in, turnstile/facility access)
    - Packages (purchased, expired)
    - Payments (completed, failed)
    - Notifications (sent)
    """
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    permission_app = 'retention'
    permission_resource = 'metrics'

    def get(self, request, client_id):
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        if not tenant:
            return Response({"detail": "Tenant context required."}, status=status.HTTP_400_BAD_REQUEST)

        # Enforce tenant isolation: target client must belong to the active tenant
        try:
            client = User.objects.get(id=client_id, tenant=tenant)
        except User.DoesNotExist:
            return Response({"detail": "Client not found in this studio."}, status=status.HTTP_404_NOT_FOUND)

        try:
            limit = int(request.query_params.get('limit', 50))
            limit = max(1, min(limit, 200))
        except (ValueError, TypeError):
            limit = 50

        try:
            offset = int(request.query_params.get('offset', 0))
            offset = max(0, offset)
        except (ValueError, TypeError):
            offset = 0

        event_filter = request.query_params.get('event_type', 'all').lower()
        events = []
        now = timezone.now()

        # 1. Bookings
        if event_filter in ['all', 'booking']:
            bookings = (
                Booking.all_objects.filter(tenant=tenant, client=client)
                .select_related('session', 'session__template', 'spot')
            )
            for b in bookings:
                template_name = b.session.template.name if b.session and b.session.template else "Class"
                session_time = b.session.start_at.strftime("%Y-%m-%d %H:%M") if b.session and b.session.start_at else ""
                spot_label = b.spot.label if b.spot else ""

                if b.created_at:
                    events.append({
                        "id": f"booking-created-{b.id}",
                        "event_type": "booking_created",
                        "timestamp": b.created_at.isoformat(),
                        "title": f"Booked: {template_name}",
                        "description": f"Reserved slot for {template_name} on {session_time}{f' (Spot: {spot_label})' if spot_label else ''}".strip(),
                        "metadata": {
                            "booking_id": str(b.id),
                            "session_id": str(b.session_id) if b.session_id else None,
                            "status": b.status,
                            "spot": spot_label,
                        }
                    })

                if b.status == 'cancelled':
                    cancel_ts = b.cancelled_at or b.updated_at
                    if cancel_ts:
                        events.append({
                            "id": f"booking-cancelled-{b.id}",
                            "event_type": "booking_cancelled",
                            "timestamp": cancel_ts.isoformat(),
                            "title": f"Cancelled: {template_name}",
                            "description": f"Cancelled {'(Late cancel)' if b.is_late_cancel else '(Early cancel)'}. {f'Reason: {b.cancellation_reason}' if b.cancellation_reason else ''}".strip(),
                            "metadata": {
                                "booking_id": str(b.id),
                                "is_late_cancel": b.is_late_cancel,
                                "cancellation_reason": b.cancellation_reason,
                            }
                        })
                elif b.status == 'no_show':
                    no_show_ts = b.no_show_at or (b.session.start_at if b.session else b.updated_at)
                    if no_show_ts:
                        events.append({
                            "id": f"booking-no-show-{b.id}",
                            "event_type": "booking_no_show",
                            "timestamp": no_show_ts.isoformat(),
                            "title": f"No-Show: {template_name}",
                            "description": f"Missed scheduled class session on {session_time}".strip(),
                            "metadata": {
                                "booking_id": str(b.id),
                                "session_id": str(b.session_id) if b.session_id else None,
                            }
                        })

        # 2. Check-ins
        if event_filter in ['all', 'checkin']:
            attended_bks = (
                Booking.all_objects.filter(
                    tenant=tenant, client=client, checked_in_at__isnull=False
                ).select_related('session', 'session__template', 'spot')
            )
            for b in attended_bks:
                template_name = b.session.template.name if b.session and b.session.template else "Class"
                events.append({
                    "id": f"checkin-class-{b.id}",
                    "event_type": "class_checked_in",
                    "timestamp": b.checked_in_at.isoformat(),
                    "title": f"Attended: {template_name}",
                    "description": f"Checked in for {template_name}",
                    "metadata": {
                        "booking_id": str(b.id),
                        "checked_out_at": b.checked_out_at.isoformat() if b.checked_out_at else None,
                    }
                })

            facility_logs = (
                FacilityAccessLog.all_objects.filter(
                    tenant=tenant, client=client
                ).select_related('location')
            )
            for fl in facility_logs:
                loc_name = fl.location.name if fl.location else "Gym"
                if fl.checked_in_at:
                    events.append({
                        "id": f"checkin-facility-{fl.id}",
                        "event_type": "facility_checkin",
                        "timestamp": fl.checked_in_at.isoformat(),
                        "title": f"Facility Entry: {loc_name}",
                        "description": f"Front-desk / Turnstile entry at {loc_name}",
                        "metadata": {
                            "location_id": str(fl.location_id) if fl.location_id else None,
                            "checked_out_at": fl.checked_out_at.isoformat() if fl.checked_out_at else None,
                        }
                    })

        # 3. Packages
        if event_filter in ['all', 'package']:
            pkgs = Package.all_objects.filter(tenant=tenant, client=client).select_related('package_type')
            for p in pkgs:
                pkg_name = p.package_type.name if p.package_type else "Package"
                if p.purchased_at:
                    events.append({
                        "id": f"package-purchased-{p.id}",
                        "event_type": "package_purchased",
                        "timestamp": p.purchased_at.isoformat(),
                        "title": f"Package Added: {pkg_name}",
                        "description": f"Granted {p.total_credits_allocated or p.credits_remaining} credits via {p.grant_source}. Valid until {p.expires_at.date()}",
                        "metadata": {
                            "package_id": str(p.id),
                            "grant_source": p.grant_source,
                            "credits_allocated": p.total_credits_allocated,
                            "credits_remaining": p.credits_remaining,
                            "price": str(p.price) if p.price is not None else None,
                        }
                    })
                if p.expires_at and p.expires_at < now:
                    events.append({
                        "id": f"package-expired-{p.id}",
                        "event_type": "package_expired",
                        "timestamp": p.expires_at.isoformat(),
                        "title": f"Package Expired: {pkg_name}",
                        "description": f"Expired with {p.credits_remaining} unused credits",
                        "metadata": {
                            "package_id": str(p.id),
                            "credits_remaining": p.credits_remaining,
                        }
                    })

        # 4. Payments
        if event_filter in ['all', 'payment']:
            payments = Payment.all_objects.filter(tenant=tenant, client=client)
            for pay in payments:
                if pay.created_at:
                    events.append({
                        "id": f"payment-{pay.id}",
                        "event_type": "payment_completed" if pay.status == 'completed' else f"payment_{pay.status}",
                        "timestamp": pay.created_at.isoformat(),
                        "title": f"Payment {pay.status.capitalize()}: ${pay.amount}",
                        "description": f"{pay.type} (${pay.amount})",
                        "metadata": {
                            "payment_id": str(pay.id),
                            "amount": str(pay.amount),
                            "status": pay.status,
                            "type": pay.type,
                            "provider_ref": pay.provider_ref,
                        }
                    })

        # 5. Notifications
        if event_filter in ['all', 'notification']:
            inbox_items = (
                NotificationInbox.all_objects.filter(tenant=tenant, recipient=client)
                .order_by('-created_at')[:100]
            )
            for item in inbox_items:
                if item.created_at:
                    events.append({
                        "id": f"notif-{item.id}",
                        "event_type": "notification_sent",
                        "timestamp": item.created_at.isoformat(),
                        "title": f"Notification: {item.title}",
                        "description": item.body[:150],
                        "metadata": {
                            "inbox_id": str(item.id),
                            "notification_type": item.notification_type,
                            "delivery_policy": item.delivery_policy,
                            "is_read": item.is_read,
                        }
                    })

        # Sort combined timeline descending by timestamp
        events.sort(key=lambda x: x.get('timestamp') or '', reverse=True)
        total_count = len(events)
        results = events[offset: offset + limit]

        return Response({
            "count": total_count,
            "limit": limit,
            "offset": offset,
            "results": results
        }, status=status.HTTP_200_OK)


class RetentionOverviewDashboardView(APIView):
    """
    Studio Retention Overview & Period-over-Period Analytics Endpoint.
    Powers Phase 1 executive retention dashboards benchmarked on Momence & Mindbody.
    """
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    permission_app = 'retention'
    permission_resource = 'metrics'

    def get(self, request):
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        if not tenant:
            return Response({"detail": "Tenant context required."}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        today = now.date()

        try:
            days = int(request.query_params.get('days', 30))
            days = max(1, min(days, 365))
        except (ValueError, TypeError):
            days = 30

        # Ensure today's snapshot exists
        try:
            RetentionMetricsService.capture_daily_snapshot(tenant, today)
        except Exception as e:
            logger.warning(f"Could not auto-capture today's snapshot: {e}")

        curr_start = now - timedelta(days=days)
        curr_end = now
        prev_start = now - timedelta(days=2 * days)
        prev_end = now - timedelta(days=days)

        with bypass_tenant_isolation():
            # 1. Lifecycle Breakdown
            client_qs = User.objects.filter(tenant=tenant, role=UserRole.CLIENT)
            lifecycle_counts = client_qs.aggregate(
                lead=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.LEAD)),
                trial=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.TRIAL)),
                active=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.ACTIVE)),
                at_risk=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.AT_RISK)),
                inactive=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.INACTIVE)),
                churned=Count('id', filter=Q(lifecycle_status=ClientLifecycleStatus.CHURNED)),
                reactivated_in_period=Count('id', filter=Q(reactivated_at__gte=curr_start)),
            )

            total_active = lifecycle_counts['active'] or 0
            total_at_risk = lifecycle_counts['at_risk'] or 0
            total_inactive = lifecycle_counts['inactive'] or 0
            total_churned = lifecycle_counts['churned'] or 0
            active_pool = total_active + total_at_risk + total_inactive + total_churned

            if active_pool > 0:
                retention_rate = round(Decimal(str(total_active + total_at_risk)) / Decimal(str(active_pool)) * Decimal('100.0'), 2)
                churn_rate = round(Decimal(str(total_inactive + total_churned)) / Decimal(str(active_pool)) * Decimal('100.0'), 2)
            else:
                retention_rate = Decimal('0.00')
                churn_rate = Decimal('0.00')

            metrics_agg = ClientRetentionMetrics.all_objects.filter(tenant=tenant).aggregate(
                avg_visit_freq=Coalesce(Avg('visit_frequency_weekly_30d'), Value(Decimal('0.00'))),
                avg_credit_util=Coalesce(Avg('credit_utilization_rate'), Value(Decimal('0.00'))),
            )

            # 2. Current Window Attendance Aggregates
            curr_attended_bk = Booking.all_objects.filter(
                tenant=tenant,
                status__in=['checked_in', 'attended']
            ).filter(
                Q(checked_in_at__gte=curr_start, checked_in_at__lte=curr_end) |
                Q(checked_in_at__isnull=True, session__start_at__gte=curr_start, session__start_at__lte=curr_end)
            ).count()

            curr_facility = FacilityAccessLog.all_objects.filter(
                tenant=tenant,
                checked_in_at__gte=curr_start,
                checked_in_at__lte=curr_end
            ).count()
            curr_total_attended = curr_attended_bk + curr_facility

            curr_total_bookings = Booking.all_objects.filter(
                tenant=tenant,
                created_at__gte=curr_start,
                created_at__lte=curr_end
            ).count()

            curr_cancellations = Booking.all_objects.filter(
                tenant=tenant,
                status='cancelled'
            ).filter(
                Q(cancelled_at__gte=curr_start, cancelled_at__lte=curr_end) |
                Q(cancelled_at__isnull=True, updated_at__gte=curr_start, updated_at__lte=curr_end)
            ).count()

            curr_late_cancellations = Booking.all_objects.filter(
                tenant=tenant,
                status='cancelled',
                is_late_cancel=True
            ).filter(
                Q(cancelled_at__gte=curr_start, cancelled_at__lte=curr_end) |
                Q(cancelled_at__isnull=True, updated_at__gte=curr_start, updated_at__lte=curr_end)
            ).count()

            curr_no_shows = Booking.all_objects.filter(
                tenant=tenant,
                status='no_show'
            ).filter(
                Q(no_show_at__gte=curr_start, no_show_at__lte=curr_end) |
                Q(no_show_at__isnull=True, session__start_at__gte=curr_start, session__start_at__lte=curr_end)
            ).count()

            curr_conv_rate = (
                round(Decimal(str(curr_attended_bk)) / Decimal(str(curr_total_bookings)) * Decimal('100.0'), 2)
                if curr_total_bookings > 0 else Decimal('0.00')
            )

            # 3. Previous Window Attendance Aggregates
            prev_attended_bk = Booking.all_objects.filter(
                tenant=tenant,
                status__in=['checked_in', 'attended']
            ).filter(
                Q(checked_in_at__gte=prev_start, checked_in_at__lte=prev_end) |
                Q(checked_in_at__isnull=True, session__start_at__gte=prev_start, session__start_at__lte=prev_end)
            ).count()

            prev_facility = FacilityAccessLog.all_objects.filter(
                tenant=tenant,
                checked_in_at__gte=prev_start,
                checked_in_at__lte=prev_end
            ).count()
            prev_total_attended = prev_attended_bk + prev_facility

            prev_total_bookings = Booking.all_objects.filter(
                tenant=tenant,
                created_at__gte=prev_start,
                created_at__lte=prev_end
            ).count()

            prev_cancellations = Booking.all_objects.filter(
                tenant=tenant,
                status='cancelled'
            ).filter(
                Q(cancelled_at__gte=prev_start, cancelled_at__lte=prev_end) |
                Q(cancelled_at__isnull=True, updated_at__gte=prev_start, updated_at__lte=prev_end)
            ).count()

            prev_late_cancellations = Booking.all_objects.filter(
                tenant=tenant,
                status='cancelled',
                is_late_cancel=True
            ).filter(
                Q(cancelled_at__gte=prev_start, cancelled_at__lte=prev_end) |
                Q(cancelled_at__isnull=True, updated_at__gte=prev_start, updated_at__lte=prev_end)
            ).count()

            prev_no_shows = Booking.all_objects.filter(
                tenant=tenant,
                status='no_show'
            ).filter(
                Q(no_show_at__gte=prev_start, no_show_at__lte=prev_end) |
                Q(no_show_at__isnull=True, session__start_at__gte=prev_start, session__start_at__lte=prev_end)
            ).count()

            def calc_delta(curr, prev):
                if prev == 0:
                    return 100.0 if curr > 0 else 0.0
                return round(((curr - prev) / prev) * 100.0, 2)

            # 4. Membership Expiry & Under-Utilization Alerts
            exp_7 = Package.all_objects.filter(
                tenant=tenant, status='active', credits_remaining__gt=0,
                expires_at__gte=now, expires_at__lte=now + timedelta(days=7)
            ).count()
            exp_14 = Package.all_objects.filter(
                tenant=tenant, status='active', credits_remaining__gt=0,
                expires_at__gte=now, expires_at__lte=now + timedelta(days=14)
            ).count()
            exp_30 = Package.all_objects.filter(
                tenant=tenant, status='active', credits_remaining__gt=0,
                expires_at__gte=now, expires_at__lte=now + timedelta(days=30)
            ).count()

            underutilized = ClientRetentionMetrics.all_objects.filter(
                tenant=tenant,
                active_packages_count__gt=0,
                credit_utilization_rate__lt=Decimal('30.00'),
                total_credits_remaining__gt=0
            ).count()

            # 5. Historical Trend Series
            snapshots = TenantRetentionDailySnapshot.all_objects.filter(
                tenant=tenant,
                snapshot_date__gte=today - timedelta(days=days),
                snapshot_date__lte=today
            ).order_by('snapshot_date')
            trend_data = TenantRetentionDailySnapshotSerializer(snapshots, many=True).data

        payload = {
            "period_days": days,
            "as_of": now.isoformat(),
            "lifecycle_breakdown": {
                "lead": lifecycle_counts['lead'] or 0,
                "trial": lifecycle_counts['trial'] or 0,
                "active": total_active,
                "at_risk": total_at_risk,
                "inactive": total_inactive,
                "churned": total_churned,
                "reactivated_in_period": lifecycle_counts['reactivated_in_period'] or 0,
            },
            "retention_kpis": {
                "retention_rate_percent": float(retention_rate),
                "churn_rate_percent": float(churn_rate),
                "avg_visit_frequency_weekly": float(metrics_agg['avg_visit_freq']),
                "avg_credit_utilization_rate_percent": float(metrics_agg['avg_credit_util']),
            },
            "attendance_summary": {
                "current_period": {
                    "total_attended": curr_total_attended,
                    "attended_classes": curr_attended_bk,
                    "facility_checkins": curr_facility,
                    "total_bookings": curr_total_bookings,
                    "cancellations": curr_cancellations,
                    "late_cancellations": curr_late_cancellations,
                    "no_shows": curr_no_shows,
                    "booking_conversion_rate_percent": float(curr_conv_rate),
                },
                "previous_period": {
                    "total_attended": prev_total_attended,
                    "attended_classes": prev_attended_bk,
                    "facility_checkins": prev_facility,
                    "total_bookings": prev_total_bookings,
                    "cancellations": prev_cancellations,
                    "late_cancellations": prev_late_cancellations,
                    "no_shows": prev_no_shows,
                },
                "deltas_percent": {
                    "total_attended_delta": calc_delta(curr_total_attended, prev_total_attended),
                    "cancellations_delta": calc_delta(curr_cancellations, prev_cancellations),
                    "late_cancellations_delta": calc_delta(curr_late_cancellations, prev_late_cancellations),
                    "no_shows_delta": calc_delta(curr_no_shows, prev_no_shows),
                }
            },
            "membership_alerts": {
                "expiring_in_7_days_count": exp_7,
                "expiring_in_14_days_count": exp_14,
                "expiring_in_30_days_count": exp_30,
                "underutilized_members_count": underutilized,
            },
            "trend_series": trend_data
        }

        return Response(payload, status=status.HTTP_200_OK)


class CustomerJourneyFunnelView(APIView):
    """
    Customer Journey Funnel & 1st-to-2nd Visit Conversion API.
    Exposed at GET /api/v1/retention/analytics/funnel/?days=90
    """
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    permission_app = 'retention'
    permission_resource = 'analytics'

    def get(self, request):
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        if not tenant:
            return Response({"detail": "Tenant context required."}, status=status.HTTP_400_BAD_REQUEST)

        days_param = request.query_params.get('days', 90)
        data = FunnelAnalyticsService.get_customer_journey_funnel(tenant=tenant, days=days_param)
        return Response(data, status=status.HTTP_200_OK)


class CohortRetentionMatrixView(APIView):
    """
    Cohort Retention Matrix API.
    Exposed at GET /api/v1/retention/analytics/cohorts/?months=6
    """
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    permission_app = 'retention'
    permission_resource = 'analytics'

    def get(self, request):
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        if not tenant:
            return Response({"detail": "Tenant context required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            months = int(request.query_params.get('months', 6))
            months = max(1, min(months, 36))
        except (ValueError, TypeError):
            months = 6

        data = CohortAnalyticsService.get_cohort_retention_matrix(tenant=tenant, months=months)
        return Response(data, status=status.HTTP_200_OK)


class ClassUtilizationAnalyticsView(APIView):
    """
    Studio Class Utilization Analytics API.
    Exposed at GET /api/v1/retention/analytics/class-utilization/?days=30
    """
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    permission_app = 'retention'
    permission_resource = 'analytics'

    def get(self, request):
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        if not tenant:
            return Response({"detail": "Tenant context required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            days = int(request.query_params.get('days', 30))
            days = max(1, min(days, 365))
        except (ValueError, TypeError):
            days = 30

        data = OperationalAnalyticsService.get_class_utilization(tenant=tenant, days=days)
        return Response(data, status=status.HTTP_200_OK)


class StaffPerformanceAnalyticsView(APIView):
    """
    Staff / Trainer Retention & Performance Analytics API.
    Exposed at GET /api/v1/retention/analytics/staff-performance/?days=30
    """
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    permission_app = 'retention'
    permission_resource = 'analytics'

    def get(self, request):
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        if not tenant:
            return Response({"detail": "Tenant context required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            days = int(request.query_params.get('days', 30))
            days = max(1, min(days, 365))
        except (ValueError, TypeError):
            days = 30

        data = OperationalAnalyticsService.get_staff_performance(tenant=tenant, days=days)
        return Response(data, status=status.HTTP_200_OK)


class AtRiskSummaryView(APIView):
    """
    Studio-wide At-Risk Revenue and Churn Vulnerability Summary API.
    Exposed at GET /api/v1/retention/at-risk/summary/
    """
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    permission_app = 'retention'
    permission_resource = 'metrics'

    def get(self, request):
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        if not tenant:
            return Response({"detail": "Tenant context required."}, status=status.HTTP_400_BAD_REQUEST)

        with bypass_tenant_isolation():
            at_risk_qs = ClientRetentionMetrics.all_objects.filter(
                tenant=tenant,
                risk_level__in=[ChurnRiskLevel.HIGH, ChurnRiskLevel.CRITICAL]
            )
            total_at_risk_count = at_risk_qs.count()
            total_at_risk_revenue = at_risk_qs.aggregate(
                total=Coalesce(Sum('at_risk_revenue'), Value(Decimal('0.00')))
            )['total']
            high_count = at_risk_qs.filter(risk_level=ChurnRiskLevel.HIGH).count()
            crit_count = at_risk_qs.filter(risk_level=ChurnRiskLevel.CRITICAL).count()
            high_val_at_risk = at_risk_qs.filter(is_high_value=True).count()

            tot_est_monthly = ClientRetentionMetrics.all_objects.filter(
                tenant=tenant
            ).aggregate(
                total=Coalesce(Sum('estimated_monthly_value'), Value(Decimal('0.00')))
            )['total']

            factor_counts = {}
            for factors in at_risk_qs.values_list('risk_factors', flat=True):
                if isinstance(factors, list):
                    for f in factors:
                        factor_counts[f] = factor_counts.get(f, 0) + 1

            top_risk_factors = [
                {"factor": f, "count": cnt}
                for f, cnt in sorted(factor_counts.items(), key=lambda x: x[1], reverse=True)
            ]

        return Response({
            "total_at_risk_clients_count": total_at_risk_count,
            "total_at_risk_revenue": float(total_at_risk_revenue),
            "risk_level_breakdown": {
                "high": high_count,
                "critical": crit_count,
            },
            "high_value_at_risk_count": high_val_at_risk,
            "top_risk_factors": top_risk_factors,
            "estimated_monthly_value_total": float(tot_est_monthly),
        }, status=status.HTTP_200_OK)


class RetentionCampaignTriggerViewSet(viewsets.ModelViewSet):
    """
    CRUD API for automated retention campaign triggers and intervention rules.
    Includes performance tracking endpoint linking sent actions to converted bookings and revenue.
    """
    queryset = RetentionCampaignTrigger.objects.all().select_related('target_segment', 'tenant')
    serializer_class = RetentionCampaignTriggerSerializer
    pagination_class = StandardResultsSetPagination
    permission_classes = [IsAuthenticated, IsOwnerOrManager]
    permission_app = 'retention'
    permission_resource = 'triggers'

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return RetentionCampaignTrigger.objects.none()

        tenant = getattr(self.request, 'tenant', None) or getattr(user, 'tenant', None)
        qs = RetentionCampaignTrigger.objects.filter(tenant=tenant).select_related('target_segment', 'tenant')
        return qs.annotate(
            total_actions_sent=Count('action_logs', filter=Q(action_logs__action_type=RetentionActionType.SENT))
        )

    def perform_create(self, serializer):
        tenant = getattr(self.request, 'tenant', None) or getattr(self.request.user, 'tenant', None)
        serializer.save(tenant=tenant)

    @action(detail=True, methods=['get'], url_path='performance')
    def performance(self, request, pk=None):
        """
        Returns funnel metrics and attributed ROI for a specific retention trigger:
        - Total sent, clicked, and converted
        - Conversion rate %
        - Total attributed revenue and average revenue per converted client
        - Recent conversion details
        """
        trigger = self.get_object()
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)

        with bypass_tenant_isolation():
            action_logs = RetentionCampaignActionLog.all_objects.filter(
                tenant=tenant,
                trigger=trigger
            )
            total_sent = action_logs.filter(action_type=RetentionActionType.SENT).count()
            total_clicked = action_logs.filter(action_type=RetentionActionType.CLICKED).count()
            total_converted = action_logs.filter(converted_at__isnull=False).count()

            conversion_rate = round(total_converted / total_sent * 100.0, 2) if total_sent > 0 else 0.0
 
            attributions = RetentionConversionAttribution.all_objects.filter(
                tenant=tenant,
                action_log__trigger=trigger
            ).select_related('client', 'booking', 'payment')

            total_revenue = attributions.aggregate(
                total=Coalesce(Sum('attributed_revenue'), Value(Decimal('0.00')))
            )['total']

            avg_revenue = round(total_revenue / Decimal(str(total_converted)), 2) if total_converted > 0 else Decimal('0.00')

            recent_attributions = attributions.order_by('-converted_at')[:20]
            conversions_data = [
                {
                    "id": str(attr.id),
                    "client_id": str(attr.client_id),
                    "client_name": attr.client.full_name or attr.client.email,
                    "conversion_event": attr.conversion_event,
                    "attributed_revenue": float(attr.attributed_revenue),
                    "converted_at": attr.converted_at.isoformat() if attr.converted_at else None,
                    "booking_id": str(attr.booking_id) if attr.booking_id else None,
                    "payment_id": str(attr.payment_id) if attr.payment_id else None,
                }
                for attr in recent_attributions
            ]

        return Response({
            "trigger_id": str(trigger.id),
            "name": trigger.name,
            "trigger_type": trigger.trigger_type,
            "channel": trigger.channel,
            "is_active": trigger.is_active,
            "use_ai_personalization": trigger.use_ai_personalization,
            "funnel": {
                "sent": total_sent,
                "clicked": total_clicked,
                "converted": total_converted,
                "conversion_rate_percent": conversion_rate,
            },
            "revenue": {
                "total_attributed_revenue": float(total_revenue),
                "average_revenue_per_conversion": float(avg_revenue),
            },
            "recent_conversions": conversions_data,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='execute-now')
    def execute_now(self, request, pk=None):
        """
        Manually triggers evaluation of this specific trigger immediately.
        """
        trigger = self.get_object()
        tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
        executed_count = 0

        if trigger.trigger_type == 'inactivity':
            logs = RetentionAutomationService.evaluate_inactivity_triggers(tenant=tenant)
            executed_count = len(logs)
        elif trigger.trigger_type == 'failed_payment':
            logs = RetentionAutomationService.evaluate_failed_payment_triggers(tenant=tenant)
            executed_count = len(logs)
        elif trigger.trigger_type == 'package_expiry':
            logs = RetentionAutomationService.evaluate_expiry_triggers(tenant=tenant)
            executed_count = len(logs)
        elif trigger.trigger_type == 'custom_segment':
            logs = RetentionAutomationService.evaluate_custom_segment_triggers(tenant=tenant)
            executed_count = len(logs)

        return Response({
            "detail": f"Trigger '{trigger.name}' executed.",
            "dispatched_interventions_count": executed_count,
        }, status=status.HTTP_200_OK)

