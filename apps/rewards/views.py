"""
Reward Engine Views & ViewSets

Separates Business Admin configuration/fulfillment endpoints from Client-facing
wallet and redemption interactions.
Highly optimized with query annotations and eager joins to eliminate N+1 queries.
Provides uniform pagination, filtering, search, and sorting across all tabs.
"""
from collections import OrderedDict
from rest_framework import viewsets, status, mixins, parsers, filters, permissions
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from django.db.models import Sum, Count, Q
from django.conf import settings

from apps.rewards.models import (
    RewardProgram, RewardRule, Badge, RewardTier,
    RewardWallet, RewardPointLedger, UserBadge, UserStreak,
    RewardCatalogItem, RewardRedemption, RewardTransaction,
    RewardRuleVersion, ProcessedRewardEvent, RedemptionStatus
)
from apps.rewards.serializers import (
    RewardProgramSerializer, RewardRuleSerializer, BadgeSerializer,
    RewardTierSerializer, RewardCatalogItemSerializer, RewardPointLedgerSerializer,
    UserBadgeSerializer, UserStreakSerializer, RewardWalletSerializer,
    RewardRedemptionSerializer, RedemptionCreateSerializer, PointsAdjustmentSerializer,
    RewardTransactionSerializer, RewardRuleVersionSerializer
)
from apps.rewards.permissions import (
    IsRewardAdminOrManager, IsRewardStaffOrAdmin, IsRewardClient
)
from apps.rewards.services import (
    RewardWalletService, RewardRedemptionService, ClientReferralService
)
from apps.users.models import User, UserRole
from apps.core.tenants.context import get_current_tenant


class RewardPagination(PageNumberPagination):
    """
    Standard pagination class for all Reward Engine tabs & listings.
    Default page size: 20 records.
    Supports ?page=N, ?page_size=M, and ?pagination=false bypass.
    """
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 1000

    def paginate_queryset(self, queryset, request, view=None):
        if request.query_params.get('pagination', '').lower() in ['false', '0', 'no']:
            return None
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        return Response(OrderedDict([
            ('count', self.page.paginator.count),
            ('total_pages', self.page.paginator.num_pages),
            ('current_page', self.page.number),
            ('page_size', self.get_page_size(self.request)),
            ('next', self.get_next_link()),
            ('previous', self.get_previous_link()),
            ('results', data),
        ]))


def get_request_tenant(request):
    """
    Robust tenant resolver that checks:
    1. request.tenant (set by middleware)
    2. Header: X-Tenant-Id / X-Tenant-ID
    3. request.data: tenant or tenant_id
    4. request.user.tenant
    5. active contextvars get_current_tenant()
    6. Platform admin fallback to first active tenant
    """
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        header_tenant_id = (
            request.headers.get('X-Tenant-Id')
            or request.headers.get('X-Tenant-ID')
            or request.META.get('HTTP_X_TENANT_ID')
        )
        if not header_tenant_id and hasattr(request, 'data') and hasattr(request.data, 'get'):
            header_tenant_id = request.data.get('tenant') or request.data.get('tenant_id')

        if header_tenant_id:
            try:
                from apps.core.tenants.models import Tenant
                tenant = Tenant.objects.filter(id=header_tenant_id).first()
            except Exception:
                pass

    if not tenant and getattr(request, 'user', None) and getattr(request.user, 'tenant', None):
        tenant = request.user.tenant

    if not tenant:
        tenant = get_current_tenant()

    if not tenant and getattr(request, 'user', None) and getattr(request.user, 'role', '') == UserRole.PLATFORM_ADMIN:
        from apps.core.tenants.models import Tenant
        tenant = Tenant.objects.filter(is_active=True).first()

    return tenant


# ==============================================================================
# BUSINESS / ADMIN VIEWSETS
# ==============================================================================

class AdminRewardProgramViewSet(viewsets.ModelViewSet):
    """
    CRUD management for tenant reward programs.
    Annotates rules_count to eliminate N+1 queries.
    """
    serializer_class = RewardProgramSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description', 'program_type']
    ordering_fields = ['name', 'status', 'program_type', 'created_at', 'updated_at']
    ordering = ['-created_at']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardProgram.all_objects.filter(tenant=tenant).annotate(rules_count=Count('rules'))
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    def perform_create(self, serializer):
        tenant = get_request_tenant(self.request)
        serializer.save(tenant=tenant)

    @action(detail=True, methods=['post'], url_path='toggle-status')
    def toggle_status(self, request, pk=None):
        program = self.get_object()
        new_status = request.data.get('status')
        if new_status not in ['draft', 'active', 'paused', 'archived']:
            return Response({'error': 'Invalid status choice.'}, status=status.HTTP_400_BAD_REQUEST)

        program.status = new_status
        program.save(update_fields=['status'])
        return Response({'id': str(program.id), 'status': program.status})


class AdminRewardRuleViewSet(viewsets.ModelViewSet):
    """
    CRUD management for dynamic reward rules.
    Eagerly loads program and version snapshots.
    """
    serializer_class = RewardRuleSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description', 'event_type', 'program__name']
    ordering_fields = ['name', 'status', 'event_type', 'priority', 'created_at', 'updated_at']
    ordering = ['-created_at']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardRule.all_objects.filter(tenant=tenant).select_related('program').prefetch_related('versions', 'versions__created_by')
        event_type = self.request.query_params.get('event_type')
        status_param = self.request.query_params.get('status')
        program_id = self.request.query_params.get('program_id')

        if event_type:
            qs = qs.filter(event_type=event_type)
        if status_param:
            qs = qs.filter(status=status_param)
        if program_id:
            qs = qs.filter(program_id=program_id)

        return qs

    def perform_create(self, serializer):
        tenant = get_request_tenant(self.request)
        serializer.save(tenant=tenant, created_by=self.request.user)

    @action(detail=True, methods=['post'], url_path='toggle-status')
    def toggle_status(self, request, pk=None):
        rule = self.get_object()
        new_status = request.data.get('status')
        if new_status not in ['draft', 'active', 'paused', 'archived']:
            return Response({'error': 'Invalid status choice.'}, status=status.HTTP_400_BAD_REQUEST)

        rule.status = new_status
        rule.save(update_fields=['status'])
        return Response({'id': str(rule.id), 'status': rule.status})


class AdminRewardRuleVersionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only view for historical rule versions.
    """
    serializer_class = RewardRuleVersionSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description', 'event_type', 'change_summary']
    ordering_fields = ['version', 'created_at']
    ordering = ['-version']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardRuleVersion.all_objects.filter(rule__tenant=tenant).select_related('created_by')
        
        rule_id = self.request.query_params.get('rule_id')
        if rule_id:
            qs = qs.filter(rule_id=rule_id)
            
        return qs


class AdminBadgeViewSet(viewsets.ModelViewSet):
    """
    CRUD management for tenant achievement badges.
    Supports JSON and multipart/form-data for direct badge image uploads.
    Annotates awarded_count to eliminate N+1 queries.
    """
    serializer_class = BadgeSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description', 'category', 'slug']
    ordering_fields = ['name', 'category', 'is_active', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = Badge.all_objects.filter(tenant=tenant).annotate(awarded_count=Count('awarded_users'))
        category = self.request.query_params.get('category')
        is_active = self.request.query_params.get('is_active')
        if category:
            qs = qs.filter(category=category)
        if is_active is not None and is_active != '':
            if is_active.lower() in ['true', '1']:
                qs = qs.filter(is_active=True)
            elif is_active.lower() in ['false', '0']:
                qs = qs.filter(is_active=False)
        return qs

    def perform_create(self, serializer):
        tenant = get_request_tenant(self.request)
        serializer.save(tenant=tenant)

    @action(detail=True, methods=['post'], url_path='upload-image', parser_classes=[parsers.MultiPartParser, parsers.FormParser])
    def upload_image(self, request, pk=None):
        """
        Dedicated endpoint to upload or update a badge image.
        Accepts 'image' or 'file' in multipart form data.
        """
        badge = self.get_object()
        image_file = request.FILES.get('image') or request.FILES.get('file')
        if not image_file:
            return Response(
                {'error': 'No image file provided. Please attach a file under the "image" or "file" field.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        badge.image = image_file
        badge.save(update_fields=['image'])
        try:
            badge.icon_url = request.build_absolute_uri(badge.image.url)
            badge.save(update_fields=['icon_url'])
        except Exception:
            pass

        return Response(BadgeSerializer(badge, context={'request': request}).data)


class RewardTierOrderingFilter(filters.OrderingFilter):
    """
    Ordering filter for Reward Tiers that safely maps legacy 'level' references
    to 'threshold_points' and ignores invalid fields to prevent FieldErrors.
    """
    def get_ordering(self, request, queryset, view):
        params = request.query_params.get(self.ordering_param)
        if params:
            fields = [param.strip() for param in params.split(',')]
            mapped_fields = [
                'threshold_points' if f == 'level' else
                ('-threshold_points' if f == '-level' else f)
                for f in fields
            ]
            ordering = self.remove_invalid_fields(queryset, mapped_fields, view, request)
            if ordering:
                return ordering
        return self.get_default_ordering(view)


class AdminRewardTierViewSet(viewsets.ModelViewSet):
    """
    CRUD management for VIP / Loyalty tiers.
    Eagerly joins badge and program.
    """
    serializer_class = RewardTierSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, RewardTierOrderingFilter]
    search_fields = ['name', 'perks_description', 'program__name']
    ordering_fields = ['name', 'threshold_points', 'multiplier', 'created_at']
    ordering = ['threshold_points', 'name']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardTier.all_objects.filter(tenant=tenant).select_related('badge', 'program')
        program_id = self.request.query_params.get('program_id')
        if program_id:
            qs = qs.filter(program_id=program_id)
        badge_id = self.request.query_params.get('badge_id') or self.request.query_params.get('badge')
        if badge_id:
            qs = qs.filter(badge_id=badge_id)
        level = self.request.query_params.get('level') or self.request.query_params.get('threshold_points')
        if level is not None and level != '':
            if str(level).isdigit():
                qs = qs.filter(threshold_points=int(level))
            else:
                qs = qs.filter(name__icontains=str(level))
        return qs

    def perform_create(self, serializer):
        tenant = get_request_tenant(self.request)
        serializer.save(tenant=tenant)

    def perform_update(self, serializer):
        tenant = get_request_tenant(self.request)
        serializer.save(tenant=tenant)


class AdminRewardCatalogViewSet(viewsets.ModelViewSet):
    """
    CRUD management for the tenant's rewards store catalog.
    Eagerly joins package_type and supports multipart image uploads.
    """
    serializer_class = RewardCatalogItemSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description', 'item_type', 'package_type__name']
    ordering_fields = ['name', 'points_cost', 'item_type', 'is_active', 'stock_quantity', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardCatalogItem.all_objects.filter(tenant=tenant).select_related('package_type')
        item_type = self.request.query_params.get('item_type')
        is_active = self.request.query_params.get('is_active')
        if item_type:
            qs = qs.filter(item_type=item_type)
        if is_active is not None and is_active != '':
            if is_active.lower() in ['true', '1']:
                qs = qs.filter(is_active=True)
            elif is_active.lower() in ['false', '0']:
                qs = qs.filter(is_active=False)
        return qs

    def perform_create(self, serializer):
        tenant = get_request_tenant(self.request)
        serializer.save(tenant=tenant)

    @action(detail=True, methods=['post'], parser_classes=[parsers.MultiPartParser, parsers.FormParser], url_path='upload-image')
    def upload_image(self, request, pk=None):
        item = self.get_object()
        image_file = request.FILES.get('image') or request.FILES.get('file')
        if not image_file:
            return Response({'error': 'No image or file provided in request.'}, status=status.HTTP_400_BAD_REQUEST)

        item.image = image_file
        item.save(update_fields=['image'])
        try:
            item.image_url = request.build_absolute_uri(item.image.url)
            item.save(update_fields=['image_url'])
        except Exception:
            pass

        return Response(RewardCatalogItemSerializer(item, context={'request': request}).data)


class AdminRewardRedemptionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Staff review and fulfillment of member point redemptions.
    Supports code verification, code fulfillment, approval, and cancellations.
    Eagerly joins user, catalog_item, and staff fulfiller.
    """
    serializer_class = RewardRedemptionSerializer
    permission_classes = [IsAuthenticated, IsRewardStaffOrAdmin]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['redemption_code', 'user__email', 'user__first_name', 'user__last_name', 'catalog_item__name']
    ordering_fields = ['created_at', 'points_spent', 'status', 'fulfilled_at']
    ordering = ['-created_at']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardRedemption.all_objects.filter(tenant=tenant).select_related(
            'user', 'catalog_item', 'catalog_item__package_type', 'fulfilled_by', 'granted_package', 'granted_package__package_type'
        )
        status_param = self.request.query_params.get('status')
        code = self.request.query_params.get('code')
        user_id = self.request.query_params.get('user_id')

        if status_param:
            qs = qs.filter(status=status_param)
        if code:
            qs = qs.filter(redemption_code__iexact=code.strip())
        if user_id:
            qs = qs.filter(user_id=user_id)

        return qs

    @action(detail=False, methods=['post'], url_path='verify-code')
    def verify_code(self, request):
        """
        Validates a member's redemption code at front-desk.
        """
        tenant = get_request_tenant(request)
        code = request.data.get('code')
        if not code:
            return Response({'error': 'Field "code" is required.'}, status=status.HTTP_400_BAD_REQUEST)

        result = RewardRedemptionService.verify_code(tenant_id=tenant.id, code=code)
        if not result.get('valid') and 'error' in result:
            return Response(result, status=status.HTTP_404_NOT_FOUND)
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='fulfill-by-code')
    def fulfill_by_code(self, request):
        """
        Direct fulfillment at the front desk by scanning or entering voucher code.
        """
        tenant = get_request_tenant(request)
        code = request.data.get('code')
        notes = request.data.get('notes', '')
        if not code:
            return Response({'error': 'Field "code" is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            redemption = RewardRedemptionService.fulfill_by_code(
                tenant_id=tenant.id,
                code=code,
                staff_user=request.user,
                notes=notes
            )
            return Response(RewardRedemptionSerializer(redemption).data)
        except ValueError as ex:
            return Response({'error': str(ex)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        Staff approval of a pending redemption.
        """
        tenant = get_request_tenant(request)
        notes = request.data.get('notes', '')
        try:
            redemption = RewardRedemptionService.approve_redemption(
                tenant_id=tenant.id,
                redemption_id=pk,
                staff_user=request.user,
                notes=notes
            )
            return Response(RewardRedemptionSerializer(redemption).data)
        except ValueError as ex:
            return Response({'error': str(ex)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def fulfill(self, request, pk=None):
        tenant = get_request_tenant(request)
        notes = request.data.get('notes', '')
        try:
            redemption = RewardRedemptionService.fulfill_redemption(
                tenant_id=tenant.id,
                redemption_id=pk,
                staff_user=request.user,
                notes=notes
            )
            return Response(RewardRedemptionSerializer(redemption).data)
        except ValueError as ex:
            return Response({'error': str(ex)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='cancel-and-refund')
    def cancel_and_refund(self, request, pk=None):
        tenant = get_request_tenant(request)
        reason = request.data.get('reason', 'Cancelled by staff')
        try:
            redemption = RewardRedemptionService.cancel_and_refund_redemption(
                tenant_id=tenant.id,
                redemption_id=pk,
                staff_user=request.user,
                reason=reason
            )
            return Response(RewardRedemptionSerializer(redemption).data)
        except ValueError as ex:
            return Response({'error': str(ex)}, status=status.HTTP_400_BAD_REQUEST)


class AdminRewardWalletViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """
    Staff oversight of member wallets and manual point adjustments.
    """
    serializer_class = RewardWalletSerializer
    permission_classes = [IsAuthenticated, IsRewardStaffOrAdmin]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__email', 'user__first_name', 'user__last_name', 'current_tier__name']
    ordering_fields = ['balance', 'lifetime_earned', 'lifetime_redeemed', 'created_at', 'updated_at']
    ordering = ['-lifetime_earned']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardWallet.all_objects.filter(tenant=tenant).select_related('user', 'current_tier')
        current_tier_id = self.request.query_params.get('current_tier_id')
        user_id = self.request.query_params.get('user_id')
        if current_tier_id:
            qs = qs.filter(current_tier_id=current_tier_id)
        if user_id:
            qs = qs.filter(user_id=user_id)
        return qs

    def perform_update(self, serializer):
        wallet = self.get_object()
        old_balance = wallet.balance
        new_balance = serializer.validated_data.get('balance', old_balance)
        
        if old_balance != new_balance:
            amount_diff = new_balance - old_balance
            updated_wallet = RewardWalletService.adjust_points(
                tenant_id=wallet.tenant_id,
                user=wallet.user,
                amount=amount_diff,
                reason="Manual balance update via API",
                admin_user=self.request.user
            )
            serializer.instance = updated_wallet
        else:
            serializer.save()

    @action(detail=False, methods=['post'], url_path='adjust-points')
    def adjust_points(self, request):
        tenant = get_request_tenant(request)
        serializer = PointsAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_id = serializer.validated_data['user_id']
        amount = serializer.validated_data['amount']
        reason = serializer.validated_data['reason']

        target_user = User.objects.filter(tenant=tenant, id=user_id).first()
        if not target_user:
            return Response({'error': 'Target user not found.'}, status=status.HTTP_404_NOT_FOUND)

        wallet = RewardWalletService.adjust_points(
            tenant_id=tenant.id,
            user=target_user,
            amount=amount,
            reason=reason,
            admin_user=request.user
        )

        return Response(RewardWalletSerializer(wallet).data)


class AdminRewardTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Auditable log of all rule evaluations and reward issuances.
    """
    serializer_class = RewardTransactionSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['user__email', 'user__first_name', 'user__last_name', 'rule__name', 'action_type', 'event_record__event_type']
    ordering_fields = ['created_at', 'action_type', 'result_status', 'rule_version']
    ordering = ['-created_at']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardTransaction.all_objects.filter(tenant=tenant).select_related('user', 'rule', 'event_record')
        result_status = self.request.query_params.get('result_status')
        event_type = self.request.query_params.get('event_type')
        action_type = self.request.query_params.get('action_type')
        user_id = self.request.query_params.get('user_id')
        rule_id = self.request.query_params.get('rule_id')

        if result_status:
            qs = qs.filter(result_status=result_status)
        if event_type:
            qs = qs.filter(event_record__event_type=event_type)
        if action_type:
            qs = qs.filter(action_type=action_type)
        if user_id:
            qs = qs.filter(user_id=user_id)
        if rule_id:
            qs = qs.filter(rule_id=rule_id)

        return qs


class AdminRewardAnalyticsView(APIView):
    """
    High-level engagement and points liability analytics using single aggregate query.
    """
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]

    def get(self, request):
        tenant = get_request_tenant(request)

        wallets_agg = RewardWallet.all_objects.filter(tenant=tenant).aggregate(
            total_active_points=Sum('balance'),
            total_lifetime_earned=Sum('lifetime_earned'),
            total_lifetime_redeemed=Sum('lifetime_redeemed'),
            member_count=Count('id')
        )

        active_rules_count = RewardRule.all_objects.filter(tenant=tenant, status='active').count()
        badges_awarded_count = UserBadge.all_objects.filter(tenant=tenant).count()
        pending_redemptions_count = RewardRedemption.all_objects.filter(tenant=tenant, status='PENDING').count()

        top_members = list(RewardWallet.all_objects.filter(tenant=tenant).select_related('user').order_by('-lifetime_earned')[:5].values(
            'user__email', 'balance', 'lifetime_earned'
        ))

        successful_rules = list(RewardTransaction.all_objects.filter(
            tenant=tenant, result_status='SUCCESS'
        ).values('rule__name', 'rule__program__name').annotate(
            execution_count=Count('id')
        ).order_by('-execution_count')[:5])

        return Response({
            'total_active_points_liability': wallets_agg['total_active_points'] or 0,
            'total_points_ever_earned': wallets_agg['total_lifetime_earned'] or 0,
            'total_points_ever_redeemed': wallets_agg['total_lifetime_redeemed'] or 0,
            'members_with_wallets': wallets_agg['member_count'] or 0,
            'active_reward_rules': active_rules_count,
            'total_badges_awarded': badges_awarded_count,
            'pending_redemptions': pending_redemptions_count,
            'top_members': top_members,
            'successful_rules': successful_rules,
        })


# ==============================================================================
# CLIENT / MEMBER-FACING VIEWS
# ==============================================================================

class ClientRewardWalletView(APIView):
    """
    Retrieves the authenticated member's point balance, tier status, and next tier goal.
    """
    permission_classes = [IsAuthenticated, IsRewardClient]

    def get(self, request):
        user = request.user
        tenant = get_request_tenant(request)

        wallet = RewardWalletService.get_or_create_wallet(tenant_id=tenant.id, user=user)

        # Calculate next tier goal if available
        next_tier = RewardTier.all_objects.filter(
            tenant=tenant,
            threshold_points__gt=wallet.lifetime_earned
        ).order_by('threshold_points').first()

        next_tier_info = None
        if next_tier:
            points_needed = next_tier.threshold_points - wallet.lifetime_earned
            next_tier_info = {
                'name': next_tier.name,
                'target_points': next_tier.threshold_points,
                'points_needed': points_needed,
                'multiplier': float(next_tier.multiplier)
            }

        return Response({
            'balance': wallet.balance,
            'lifetime_earned': wallet.lifetime_earned,
            'lifetime_redeemed': wallet.lifetime_redeemed,
            'current_tier': {
                'name': wallet.current_tier.name if wallet.current_tier else "Standard Member",
                'multiplier': float(wallet.current_tier.multiplier) if wallet.current_tier else 1.0,
                'perks': wallet.current_tier.perks_description if wallet.current_tier else ""
            },
            'next_tier': next_tier_info
        })


class ClientRewardLedgerView(APIView):
    """
    Paginated audit history of points earned and spent by the client.
    """
    permission_classes = [IsAuthenticated, IsRewardClient]

    def get(self, request):
        tenant = get_request_tenant(request)
        qs = RewardPointLedger.all_objects.filter(
            tenant=tenant,
            user=request.user
        )
        tx_type = request.query_params.get('transaction_type') or request.query_params.get('entry_type')
        if tx_type:
            qs = qs.filter(transaction_type__iexact=tx_type.strip())

        search = request.query_params.get('search')
        if search:
            qs = qs.filter(Q(description__icontains=search) | Q(transaction_type__icontains=search))

        ordering = request.query_params.get('ordering', '-created_at')
        if ordering in ['created_at', '-created_at', 'amount', '-amount', 'balance_after', '-balance_after']:
            qs = qs.order_by(ordering)
        else:
            qs = qs.order_by('-created_at')

        paginator = RewardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            serializer = RewardPointLedgerSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = RewardPointLedgerSerializer(qs, many=True)
        return Response(serializer.data)


class ClientBadgeView(APIView):
    """
    Returns earned and available badges for the authenticated client without N+1 queries.
    """
    permission_classes = [IsAuthenticated, IsRewardClient]

    def get(self, request):
        user = request.user
        tenant = get_request_tenant(request)

        earned_user_badges = UserBadge.all_objects.filter(
            tenant=tenant,
            user=user
        ).select_related('badge')

        earned_badge_ids = {ub.badge_id for ub in earned_user_badges}

        available_badges = Badge.all_objects.filter(
            tenant=tenant,
            is_active=True
        ).annotate(awarded_count=Count('awarded_users'))

        return Response({
            'earned_badges': UserBadgeSerializer(earned_user_badges, many=True, context={'request': request}).data,
            'all_badges': BadgeSerializer(available_badges, many=True, context={'request': request}).data,
            'total_earned': len(earned_badge_ids),
            'total_available': available_badges.count()
        })


class ClientStreakView(APIView):
    """
    Returns the client's current and longest streaks.
    """
    permission_classes = [IsAuthenticated, IsRewardClient]

    def get(self, request):
        tenant = get_request_tenant(request)
        streaks = UserStreak.all_objects.filter(
            tenant=tenant,
            user=request.user
        )
        return Response(UserStreakSerializer(streaks, many=True).data)


class ClientRewardStoreViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Client catalog of redeemable reward store items with affordability calculation.
    Eagerly loads package_type relations.
    """
    serializer_class = RewardCatalogItemSerializer
    permission_classes = [IsAuthenticated, IsRewardClient]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'description']
    ordering_fields = ['points_cost', 'name', 'created_at']
    ordering = ['points_cost']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardCatalogItem.all_objects.filter(
            tenant=tenant,
            is_active=True
        ).filter(
            Q(stock_quantity__isnull=True) | Q(stock_quantity__gt=0)
        ).select_related('package_type')

        item_type = self.request.query_params.get('item_type')
        if item_type:
            qs = qs.filter(item_type=item_type)

        return qs

    def list(self, request, *args, **kwargs):
        tenant = get_request_tenant(request)
        queryset = self.filter_queryset(self.get_queryset())
        wallet = RewardWalletService.get_or_create_wallet(tenant_id=tenant.id, user=request.user)

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            items_data = serializer.data
            for item in items_data:
                item['can_afford'] = wallet.balance >= item['points_cost']
                item['points_remaining_needed'] = max(0, item['points_cost'] - wallet.balance)

            response = self.get_paginated_response(items_data)
            response.data['wallet_balance'] = wallet.balance
            response.data['catalog_items'] = items_data
            return response

        serializer = self.get_serializer(queryset, many=True)
        items_data = serializer.data

        # Add client affordability indicator
        for item in items_data:
            item['can_afford'] = wallet.balance >= item['points_cost']
            item['points_remaining_needed'] = max(0, item['points_cost'] - wallet.balance)

        return Response({
            'wallet_balance': wallet.balance,
            'catalog_items': items_data,
            'results': items_data
        })


class ClientRedemptionViewSet(viewsets.ModelViewSet):
    """
    Member redemption submission and personal voucher history.
    Eagerly joins catalog_item and package_type.
    """
    serializer_class = RewardRedemptionSerializer
    permission_classes = [IsAuthenticated, IsRewardClient]
    pagination_class = RewardPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['redemption_code', 'catalog_item__name']
    ordering_fields = ['created_at', 'points_spent', 'status']
    ordering = ['-created_at']

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardRedemption.all_objects.filter(
            tenant=tenant,
            user=self.request.user
        ).select_related('catalog_item', 'catalog_item__package_type', 'fulfilled_by')
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    def create(self, request, *args, **kwargs):
        tenant = get_request_tenant(request)
        serializer = RedemptionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        catalog_item_id = serializer.validated_data['catalog_item_id']
        notes = serializer.validated_data.get('notes', '')

        try:
            redemption = RewardRedemptionService.redeem_item(
                tenant_id=tenant.id,
                user=request.user,
                catalog_item_id=catalog_item_id,
                notes=notes
            )
            return Response(
                RewardRedemptionSerializer(redemption).data,
                status=status.HTTP_201_CREATED
            )
        except ValueError as ex:
            return Response({'error': str(ex)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        """
        Allows a member to cancel their own pending voucher before fulfillment.
        Refunds points, updates ledger, and restores stock.
        """
        tenant = get_request_tenant(request)
        redemption = self.get_object()
        if redemption.status != RedemptionStatus.PENDING:
            return Response(
                {'error': f'Only pending redemptions can be cancelled. Current status: {redemption.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        reason = request.data.get('reason', 'Cancelled by member')
        try:
            cancelled = RewardRedemptionService.cancel_and_refund_redemption(
                tenant_id=tenant.id,
                redemption_id=redemption.id,
                staff_user=request.user,
                reason=reason
            )
            return Response(RewardRedemptionSerializer(cancelled).data)
        except ValueError as ex:
            return Response({'error': str(ex)}, status=status.HTTP_400_BAD_REQUEST)


class ClientReferralView(APIView):
    """
    Client Referral Management & Submission API.
    Provides members with their referral code/link, and processes referral submissions.
    Supports Option 1 (In-App Referral Code Flow) and friend invitations.
    Strictly prevents self-referrals and never creates unverified user accounts.
    """
    permission_classes = [IsAuthenticated, IsRewardClient]

    def get(self, request):
        tenant = get_request_tenant(request)
        user = request.user
        profile = getattr(user, 'profile', None)
        code = profile.get_or_create_referral_code() if profile else f"REF-{user.id.hex[:8].upper()}"
        base_url = request.build_absolute_uri('/')[:-1]
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')

        from apps.rewards.models import ClientReferral
        referral_count = ClientReferral.objects.filter(
            tenant=tenant,
            referrer=user,
            status=ClientReferral.ReferralStatus.COMPLETED
        ).count()
        if referral_count == 0:
            referral_count = ProcessedRewardEvent.all_objects.filter(
                tenant=tenant,
                event_type='referral.completed',
                payload__referrer_id=str(user.id)
            ).count()

        universal_link = f"{base_url}/api/v1/rewards/referrals/join/?ref={code}"
        web_link = f"{frontend_url}/join?ref={code}"
        deep_link = f"fitverx://join?ref={code}"

        return Response({
            'referrer_id': str(user.id),
            'referrer_name': user.full_name,
            'referrer_email': user.email,
            'referral_code': code,
            'referral_link': universal_link,
            'web_referral_link': web_link,
            'deep_link': deep_link,
            'total_referrals_completed': referral_count,
            'description': "Share your referral code with friends. When they join, you earn reward points and package credits."
        })

    def post(self, request):
        tenant = get_request_tenant(request)
        user = request.user

        referral_code = request.data.get('referral_code')
        referee_email = request.data.get('referee_email')
        referee_id = request.data.get('referee_id')

        if not referral_code and not referee_email and not referee_id:
            return Response(
                {'error': 'Either referral_code or referee_email is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # FLOW 1: Current user is entering someone else's referral code (Option 1)
        if referral_code:
            try:
                result = ClientReferralService.process_referral(
                    tenant=tenant,
                    referee=user,
                    referral_code=str(referral_code).strip(),
                    method='CODE'
                )
                return Response({
                    'status': 'success',
                    'message': 'Referral completed successfully.',
                    **result
                }, status=status.HTTP_201_CREATED)
            except Exception as ex:
                err_msg = ex.message if hasattr(ex, 'message') else str(ex)
                return Response({'error': err_msg}, status=status.HTTP_400_BAD_REQUEST)

        # FLOW 2: Current user is inviting a friend by email (Registration-First, Consent-Based)
        if referee_email:
            clean_email = str(referee_email).strip().lower()
            if clean_email == user.email.lower():
                return Response({'error': 'Self-referrals are not permitted.'}, status=status.HTTP_400_BAD_REQUEST)

            existing_user = User.objects.filter(email=clean_email, tenant=tenant).first()
            if existing_user:
                return Response({
                    'error': 'A member with this email already belongs to this gym. Ask them to enter your referral code in their app.'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Registration-First: DO NOT CREATE A USER RECORD.
            # Provide an invitation link for the referee to register with their own consent.
            profile = getattr(user, 'profile', None)
            code = profile.get_or_create_referral_code() if profile else f"REF-{user.id.hex[:8].upper()}"
            base_url = request.build_absolute_uri('/')[:-1]
            universal_link = f"{base_url}/api/v1/rewards/referrals/join/?ref={code}"

            try:
                from django.core.mail import EmailMultiAlternatives
                subject = f"{user.full_name} invited you to join {tenant.name}!"
                body = (
                    f"Hi,\n\n{user.full_name} has invited you to join {tenant.name} on FitVerx!\n\n"
                    f"Sign up using this link to receive bonus rewards: {universal_link}\n"
                    f"Or use referral code: {code}\n\nSee you there!"
                )
                msg = EmailMultiAlternatives(subject, body, settings.DEFAULT_FROM_EMAIL, [clean_email])
                msg.send(fail_silently=True)
            except Exception:
                pass

            return Response({
                'status': 'invitation_sent',
                'message': f"Invitation sent to {clean_email}. They can register using your referral link.",
                'referral_code': code,
                'referral_link': universal_link
            }, status=status.HTTP_200_OK)

        # FLOW 3: Legacy referee_id parameter (testing & backward compatibility)
        if referee_id:
            if str(referee_id) == str(user.id):
                return Response({'error': 'Self-referrals are not permitted.'}, status=status.HTTP_400_BAD_REQUEST)

            try:
                import uuid
                referee = User.objects.filter(id=uuid.UUID(str(referee_id)), tenant=tenant).first()
            except (ValueError, TypeError):
                return Response({'error': 'Invalid referee_id format.'}, status=status.HTTP_400_BAD_REQUEST)

            if not referee:
                return Response({'error': 'Referee user could not be resolved.'}, status=status.HTTP_404_NOT_FOUND)

            profile = getattr(user, 'profile', None)
            code = profile.get_or_create_referral_code() if profile else f"REF-{user.id.hex[:8].upper()}"
            try:
                result = ClientReferralService.process_referral(
                    tenant=tenant,
                    referee=referee,
                    referral_code=code,
                    method='CODE'
                )
                return Response({
                    'status': 'success',
                    'message': 'Referral completed successfully.',
                    **result
                }, status=status.HTTP_201_CREATED)
            except Exception as ex:
                err_msg = ex.message if hasattr(ex, 'message') else str(ex)
                return Response({'error': err_msg}, status=status.HTTP_400_BAD_REQUEST)


class ClientReferralApplyView(APIView):
    """
    Explicit endpoint for Option 1: A logged-in client applies a friend's referral code.
    POST /api/v1/rewards/client/referrals/apply/
    Payload: {"referral_code": "REF-..."}
    """
    permission_classes = [IsAuthenticated, IsRewardClient]

    def post(self, request):
        tenant = get_request_tenant(request)
        user = request.user
        referral_code = request.data.get('referral_code')

        if not referral_code:
            return Response({'error': 'referral_code is required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = ClientReferralService.process_referral(
                tenant=tenant,
                referee=user,
                referral_code=str(referral_code).strip(),
                method='CODE'
            )
            return Response({
                'status': 'success',
                'message': 'Referral code applied successfully. Rewards have been awarded!',
                **result
            }, status=status.HTTP_201_CREATED)
        except Exception as ex:
            err_msg = ex.message if hasattr(ex, 'message') else str(ex)
            return Response({'error': err_msg}, status=status.HTTP_400_BAD_REQUEST)


class ReferralLookupView(APIView):
    """
    Public lookup endpoint for validating referral codes in frontend / mobile apps before signup.
    GET /api/v1/rewards/referrals/lookup/?ref=REF-XXXX
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        code = request.GET.get('ref') or request.GET.get('referral_code')
        if not code:
            return Response({'valid': False, 'error': 'Referral code parameter "ref" is required.'}, status=status.HTTP_400_BAD_REQUEST)

        from apps.users.models import UserProfile
        from apps.core.tenants.context import bypass_tenant_isolation
        import re
        
        with bypass_tenant_isolation():
            raw_code = str(code).strip()
            # Clean up markdown/URL artifacts (e.g., extracting REF-F64C7900 from "REF-F64C7900](http...")
            clean_code = re.split(r'[^a-zA-Z0-9\-]', raw_code)[0].upper()
            
            profile = UserProfile.objects.filter(referral_code__iexact=clean_code, user__is_active=True).select_related('user', 'user__tenant').first()
            user = profile.user if profile else None

            if not user and clean_code.startswith("REF-"):
                prefix = clean_code[4:12].lower()
                for u in User.objects.filter(is_active=True).select_related('tenant'):
                    if u.id.hex[:8].lower() == prefix:
                        user = u
                        break

            if not user or not user.tenant:
                return Response({'valid': False, 'error': 'Invalid or expired referral code.'}, status=status.HTTP_404_NOT_FOUND)

            frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')
            return Response({
                'valid': True,
                'referral_code': clean_code,
                'referrer_id': str(user.id),
                'referrer_name': user.full_name,
                'gym_id': str(user.tenant.id),
                'gym_name': user.tenant.name,
                'gym_subdomain': user.tenant.subdomain,
                'web_registration_url': f"{frontend_url}/register?ref={clean_code}&tenant_id={user.tenant.id}",
                'app_deep_link': f"fitverx://join?ref={clean_code}&tenant_id={user.tenant.id}"
            }, status=status.HTTP_200_OK)


from django.views import View


class ReferralJoinRedirectView(View):
    """
    Universal Link & Smart Redirect View for Referral Links.
    GET /api/v1/rewards/referrals/join/?ref=REF-XXXX
    Redirects mobile users to app scheme / app stores, or web users to web registration.
    """
    def get(self, request):
        code = request.GET.get('ref') or request.GET.get('referral_code', '')
        
        import re
        raw_code = str(code).strip() if code else ''
        clean_code = re.split(r'[^a-zA-Z0-9\-]', raw_code)[0].upper() if raw_code else ''

        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')
        target_web_url = f"{frontend_url}/register?ref={clean_code}" if clean_code else f"{frontend_url}/register"
        app_deep_link = f"fitverx://join?ref={clean_code}" if clean_code else "fitverx://join"

        user_agent = request.META.get('HTTP_USER_AGENT', '').lower()
        is_mobile = any(keyword in user_agent for keyword in ['iphone', 'ipad', 'android', 'mobile'])

        if not is_mobile:
            from django.shortcuts import redirect
            return redirect(target_web_url)

        from django.http import HttpResponse
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Join Gym - FitVerx Referral</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta property="og:title" content="Join on FitVerx">
    <meta property="og:description" content="You have been invited to join with bonus rewards!">
    <script>
        window.location.href = "{app_deep_link}";
        setTimeout(function() {{
            window.location.href = "{target_web_url}";
        }}, 2000);
    </script>
</head>
<body style="font-family: sans-serif; text-align: center; padding: 40px;">
    <h2>Opening FitVerx App...</h2>
    <p>If the app does not open automatically, <a href="{target_web_url}">click here to continue on the web</a>.</p>
</body>
</html>"""
        return HttpResponse(html, content_type='text/html')

