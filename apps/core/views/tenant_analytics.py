"""
Tenant Analytics API — Platform Admin
======================================
Enterprise analytics endpoint that returns a paginated, filterable,
sortable list of all tenants on the platform with deep per-tenant metrics.

All metrics are derived from DB-level annotations (COUNT / SUM with filters),
meaning a single SQL query returns the full dataset for the page — no N+1.

Accessible at: GET /api/v1/platform/tenants/analytics/
Permission: Platform Admin only (is_staff=True, tenant=None)
"""

from django.db.models import (
    Count, Sum, Q, DecimalField, OuterRef, Subquery,
    Case, When, IntegerField, Value
)
from django.db.models.functions import Coalesce
from django.contrib.auth import get_user_model

from rest_framework import generics, serializers
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend, FilterSet, BooleanFilter, CharFilter

from apps.core.tenants.models import Tenant
from apps.core.tenants.context import bypass_tenant_isolation
from apps.core.views.platform_views import IsPlatformAdmin

User = get_user_model()

STAFF_ROLES = ['trainer', 'gym_manager', 'gym_owner', 'front_desk']

class TenantAnalyticsPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class TenantAnalyticsFilter(FilterSet):
    is_active = BooleanFilter(field_name='is_active')
    billing_plan = CharFilter(
        field_name='billing_subscriptions__billing_plan__slug',
        lookup_expr='iexact'
    )
    billing_status = CharFilter(
        field_name='billing_subscriptions__status',
        lookup_expr='iexact'
    )

    class Meta:
        model = Tenant
        fields = ['is_active', 'billing_plan', 'billing_status']


class TenantAnalyticsSerializer(serializers.Serializer):
    """
    Read-only serializer.  All numeric metrics come from DB annotations;
    nested fields (billing plan, referred_by) are resolved via
    SerializerMethodField to keep the annotation surface minimal.
    """

    tenant_id           = serializers.UUIDField(source='id')
    tenant_name         = serializers.CharField(source='name')
    subdomain           = serializers.CharField()
    is_active           = serializers.BooleanField()
    joined_at           = serializers.DateTimeField(source='created_at')
    stripe_customer_id  = serializers.CharField(allow_null=True)
    referred_by         = serializers.SerializerMethodField()
    referral_count      = serializers.IntegerField()

    total_clients       = serializers.IntegerField()
    active_clients      = serializers.IntegerField()
    inactive_clients    = serializers.IntegerField()
    total_staff         = serializers.IntegerField()
    active_staff        = serializers.IntegerField()
    inactive_staff      = serializers.IntegerField()
    gym_owners          = serializers.IntegerField()
    trainers            = serializers.IntegerField()

    total_revenue_gross     = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_platform_fees     = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_revenue_net       = serializers.DecimalField(max_digits=14, decimal_places=2)
    pending_payouts         = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_package_purchases = serializers.IntegerField()
    billing_plan            = serializers.SerializerMethodField()
    billing_status          = serializers.SerializerMethodField()
    billing_period_end      = serializers.SerializerMethodField()

    total_packages_sold     = serializers.IntegerField()
    total_bookings          = serializers.IntegerField()
    attended_bookings       = serializers.IntegerField()
    cancelled_bookings      = serializers.IntegerField()
    no_show_bookings        = serializers.IntegerField()
    total_appointments      = serializers.IntegerField()
    total_locations         = serializers.IntegerField()
    total_classes           = serializers.IntegerField()

    def get_referred_by(self, obj):
        if obj.referred_by_id:
            return {
                'id':   str(obj.referred_by_id),
                'name': obj.referred_by.name if obj.referred_by else None,
            }
        return None

    def get_billing_plan(self, obj):
        sub = self._active_billing_sub(obj)
        if sub:
            return sub.billing_plan.name if sub.billing_plan_id else None
        return None

    def get_billing_status(self, obj):
        sub = self._active_billing_sub(obj)
        return sub.status if sub else None

    def get_billing_period_end(self, obj):
        sub = self._active_billing_sub(obj)
        return sub.current_period_end if sub else None

    def _active_billing_sub(self, obj):
        """
        Returns the first billing subscription prefetched on the tenant.
        Avoids extra DB hits because the queryset prefetches billing_subscriptions.
        """
        subs = getattr(obj, '_prefetched_billing_subs', None)
        if subs is None:
            subs = list(obj.billing_subscriptions.all())
            obj._prefetched_billing_subs = subs
        return subs[0] if subs else None


def _build_analytics_queryset():
    """
    Returns a fully annotated Tenant queryset.
    Every metric is a single DB-level expression — the ORM generates
    one round-trip per page, not one per tenant.
    """
    from apps.scheduling.models import Payment, Package, Booking, Appointment, Location, ClassTemplate
    from apps.payments.models import PlatformLedger, TenantPayout

    qs = Tenant.objects.annotate(
        total_clients=Count(
            'users',
            filter=Q(users__role='client'),
            distinct=True
        ),
        active_clients=Count(
            'users',
            filter=Q(users__role='client', users__is_active=True),
            distinct=True
        ),
        inactive_clients=Count(
            'users',
            filter=Q(users__role='client', users__is_active=False),
            distinct=True
        ),
        total_staff=Count(
            'users',
            filter=Q(users__role__in=STAFF_ROLES),
            distinct=True
        ),
        active_staff=Count(
            'users',
            filter=Q(users__role__in=STAFF_ROLES, users__is_active=True),
            distinct=True
        ),
        inactive_staff=Count(
            'users',
            filter=Q(users__role__in=STAFF_ROLES, users__is_active=False),
            distinct=True
        ),
        gym_owners=Count(
            'users',
            filter=Q(users__role='gym_owner'),
            distinct=True
        ),
        trainers=Count(
            'users',
            filter=Q(users__role='trainer'),
            distinct=True
        ),

        referral_count=Count('referrals', distinct=True),

        total_revenue_gross=Coalesce(
            Sum(
                'payments__amount',
                filter=Q(payments__status='completed'),
                output_field=DecimalField()
            ),
            Value(0, output_field=DecimalField())
        ),
        total_package_purchases=Count(
            'payments',
            filter=Q(payments__type='package_purchase'),
            distinct=True
        ),

        # ── Financial — payments.PlatformLedger ───────────────────────────
        # TenantMixin related_name='%(class)ss' → reverse = 'platformledgers'
        total_platform_fees=Coalesce(
            Sum('platformledgers__platform_fee', output_field=DecimalField()),
            Value(0, output_field=DecimalField())
        ),
        total_revenue_net=Coalesce(
            Sum('platformledgers__amount_net', output_field=DecimalField()),
            Value(0, output_field=DecimalField())
        ),
        pending_payouts=Coalesce(
            Sum(
                'tenantpayouts__amount',
                filter=Q(tenantpayouts__status='pending'),
                output_field=DecimalField()
            ),
            Value(0, output_field=DecimalField())
        ),

        total_packages_sold=Count('packages', distinct=True),
        total_bookings=Count('bookings', distinct=True),
        attended_bookings=Count(
            'bookings',
            filter=Q(bookings__status='attended'),
            distinct=True
        ),
        cancelled_bookings=Count(
            'bookings',
            filter=Q(bookings__status='cancelled'),
            distinct=True
        ),
        no_show_bookings=Count(
            'bookings',
            filter=Q(bookings__status='no_show'),
            distinct=True
        ),

        total_appointments=Count('appointments', distinct=True),

        total_locations=Count('locations', distinct=True),
        total_classes=Count('classtemplates', distinct=True),

    ).select_related(
        'referred_by',
    ).prefetch_related(
        'billing_subscriptions__billing_plan',
    ).order_by('-created_at')

    return qs


class TenantAnalyticsListView(generics.ListAPIView):
    """
    GET /api/v1/platform/tenants/analytics/

    Returns paginated, annotated analytics for every tenant on the platform.
    Platform Admin access only.

    Query Params:
        ?page=N                 Page number (default 1)
        ?page_size=N            Items per page (default 20, max 100)
        ?search=gym             Filter by tenant name or subdomain
        ?is_active=true|false   Filter by tenant active status
        ?billing_plan=free      Filter by billing plan slug (free/basic/premium/custom)
        ?billing_status=active  Filter by billing subscription status
        ?ordering=field         Sort by any annotated field, prefix with - for desc
                                e.g. ?ordering=-total_clients,total_revenue_gross
    """
    serializer_class      = TenantAnalyticsSerializer
    permission_classes    = [IsPlatformAdmin]
    pagination_class      = TenantAnalyticsPagination
    filter_backends       = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class       = TenantAnalyticsFilter
    search_fields         = ['name', 'subdomain']
    ordering_fields       = [
        'created_at', 'name',
        'total_clients', 'active_clients', 'inactive_clients',
        'total_staff', 'active_staff',
        'total_revenue_gross', 'total_platform_fees', 'total_revenue_net',
        'pending_payouts', 'total_package_purchases',
        'total_bookings', 'attended_bookings',
        'total_packages_sold', 'total_appointments',
        'total_locations', 'total_classes',
        'referral_count',
    ]
    ordering = ['-created_at']

    def get_queryset(self):
        with bypass_tenant_isolation():
            return _build_analytics_queryset()

    def list(self, request, *args, **kwargs):
        with bypass_tenant_isolation():
            queryset = self.filter_queryset(self.get_queryset())

            platform_summary = self._compute_platform_summary(queryset)

            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                paginated_response = self.get_paginated_response(serializer.data)
                paginated_response.data['platform_summary'] = platform_summary
                return paginated_response

            serializer = self.get_serializer(queryset, many=True)
            return Response({
                'results': serializer.data,
                'platform_summary': platform_summary,
            })

    @staticmethod
    def _compute_platform_summary(qs):
        """
        Platform-wide aggregation injected as a top-level envelope field.
        Computed over the **filtered** queryset so numbers match what's shown.
        Uses a second aggregate() call — still one SQL query.
        """
        from django.db.models import Sum as _Sum, Count as _Count

        totals = qs.aggregate(
            total_tenants=_Count('id', distinct=True),
            active_tenants=_Count('id', filter=Q(is_active=True), distinct=True),
            inactive_tenants=_Count('id', filter=Q(is_active=False), distinct=True),
            sum_clients=_Sum('total_clients'),
            sum_active_clients=_Sum('active_clients'),
            sum_staff=_Sum('total_staff'),
            sum_revenue_gross=_Sum('total_revenue_gross'),
            sum_platform_fees=_Sum('total_platform_fees'),
            sum_revenue_net=_Sum('total_revenue_net'),
            sum_pending_payouts=_Sum('pending_payouts'),
            sum_bookings=_Sum('total_bookings'),
            sum_attended=_Sum('attended_bookings'),
        )
        return {
            'total_tenants':        totals['total_tenants']       or 0,
            'active_tenants':       totals['active_tenants']      or 0,
            'inactive_tenants':     totals['inactive_tenants']    or 0,
            'total_clients':        totals['sum_clients']         or 0,
            'active_clients':       totals['sum_active_clients']  or 0,
            'total_staff':          totals['sum_staff']           or 0,
            'total_revenue_gross':  str(totals['sum_revenue_gross']  or 0),
            'total_platform_fees':  str(totals['sum_platform_fees']  or 0),
            'total_revenue_net':    str(totals['sum_revenue_net']    or 0),
            'pending_payouts':      str(totals['sum_pending_payouts'] or 0),
            'total_bookings':       totals['sum_bookings']        or 0,
            'attended_bookings':    totals['sum_attended']        or 0,
        }
