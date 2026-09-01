"""
Reward Engine Views & ViewSets

Separates Business Admin configuration/fulfillment endpoints from Client-facing
wallet and redemption interactions.
Highly optimized with query annotations and eager joins to eliminate N+1 queries.
"""
from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Sum, Count, Q

from apps.rewards.models import (
    RewardProgram, RewardRule, Badge, RewardTier,
    RewardWallet, RewardPointLedger, UserBadge, UserStreak,
    RewardCatalogItem, RewardRedemption, RewardTransaction
)
from apps.rewards.serializers import (
    RewardProgramSerializer, RewardRuleSerializer, BadgeSerializer,
    RewardTierSerializer, RewardCatalogItemSerializer, RewardPointLedgerSerializer,
    UserBadgeSerializer, UserStreakSerializer, RewardWalletSerializer,
    RewardRedemptionSerializer, RedemptionCreateSerializer, PointsAdjustmentSerializer,
    RewardTransactionSerializer
)
from apps.rewards.permissions import (
    IsRewardAdminOrManager, IsRewardStaffOrAdmin, IsRewardClient
)
from apps.rewards.services import (
    RewardWalletService, RewardRedemptionService
)
from apps.users.models import User
from apps.core.tenants.context import get_current_tenant


def get_request_tenant(request):
    """
    Robust tenant resolver that checks request.tenant, request.user.tenant,
    or active contextvars tenant.
    """
    tenant = getattr(request, 'tenant', None)
    if not tenant and getattr(request, 'user', None) and getattr(request.user, 'tenant', None):
        tenant = request.user.tenant
    if not tenant:
        tenant = get_current_tenant()
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

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return RewardProgram.objects.filter(tenant=tenant).annotate(rules_count=Count('rules'))

    def perform_create(self, serializer):
        tenant = get_request_tenant(self.request)
        serializer.save(tenant=tenant)


class AdminRewardRuleViewSet(viewsets.ModelViewSet):
    """
    CRUD management for dynamic reward rules.
    Eagerly loads program and version snapshots.
    """
    serializer_class = RewardRuleSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardRule.objects.filter(tenant=tenant).select_related('program').prefetch_related('versions', 'versions__created_by')
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


class AdminBadgeViewSet(viewsets.ModelViewSet):
    """
    CRUD management for tenant achievement badges.
    Annotates awarded_count to eliminate N+1 queries.
    """
    serializer_class = BadgeSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return Badge.objects.filter(tenant=tenant).annotate(awarded_count=Count('user_awards'))

    def perform_create(self, serializer):
        tenant = get_request_tenant(self.request)
        serializer.save(tenant=tenant)


class AdminRewardTierViewSet(viewsets.ModelViewSet):
    """
    CRUD management for VIP / Loyalty tiers.
    Eagerly joins badge and program.
    """
    serializer_class = RewardTierSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return RewardTier.objects.filter(tenant=tenant).select_related('badge', 'program')

    def perform_create(self, serializer):
        tenant = get_request_tenant(self.request)
        serializer.save(tenant=tenant)


class AdminRewardCatalogViewSet(viewsets.ModelViewSet):
    """
    CRUD management for the tenant's rewards store catalog.
    Eagerly joins package_type.
    """
    serializer_class = RewardCatalogItemSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return RewardCatalogItem.objects.filter(tenant=tenant).select_related('package_type')

    def perform_create(self, serializer):
        tenant = get_request_tenant(self.request)
        serializer.save(tenant=tenant)


class AdminRewardRedemptionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Staff review and fulfillment of member point redemptions.
    Eagerly joins user, catalog_item, and staff fulfiller.
    """
    serializer_class = RewardRedemptionSerializer
    permission_classes = [IsAuthenticated, IsRewardStaffOrAdmin]

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardRedemption.objects.filter(tenant=tenant).select_related(
            'user', 'catalog_item', 'catalog_item__package_type', 'fulfilled_by'
        )
        status_param = self.request.query_params.get('status')
        code = self.request.query_params.get('code')

        if status_param:
            qs = qs.filter(status=status_param)
        if code:
            qs = qs.filter(redemption_code__iexact=code.strip())

        return qs

    @action(detail=True, methods=['post'])
    def fulfill(self, request, pk=None):
        tenant = get_request_tenant(request)
        try:
            redemption = RewardRedemptionService.fulfill_redemption(
                tenant_id=tenant.id,
                redemption_id=pk,
                staff_user=request.user
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


class AdminRewardWalletViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Staff oversight of member wallets and manual point adjustments.
    """
    serializer_class = RewardWalletSerializer
    permission_classes = [IsAuthenticated, IsRewardStaffOrAdmin]

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return RewardWallet.objects.filter(tenant=tenant).select_related('user', 'current_tier')

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

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return RewardTransaction.objects.filter(tenant=tenant).select_related('user', 'rule')


class AdminRewardAnalyticsView(APIView):
    """
    High-level engagement and points liability analytics using single aggregate query.
    """
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]

    def get(self, request):
        tenant = get_request_tenant(request)

        wallets_agg = RewardWallet.objects.filter(tenant=tenant).aggregate(
            total_active_points=Sum('balance'),
            total_lifetime_earned=Sum('lifetime_earned'),
            total_lifetime_redeemed=Sum('lifetime_redeemed'),
            member_count=Count('id')
        )

        active_rules_count = RewardRule.objects.filter(tenant=tenant, status='active').count()
        badges_awarded_count = UserBadge.objects.filter(tenant=tenant).count()
        pending_redemptions_count = RewardRedemption.objects.filter(tenant=tenant, status='PENDING').count()

        return Response({
            'total_active_points_liability': wallets_agg['total_active_points'] or 0,
            'total_points_ever_earned': wallets_agg['total_lifetime_earned'] or 0,
            'total_points_ever_redeemed': wallets_agg['total_lifetime_redeemed'] or 0,
            'members_with_wallets': wallets_agg['member_count'] or 0,
            'active_reward_rules': active_rules_count,
            'total_badges_awarded': badges_awarded_count,
            'pending_redemptions': pending_redemptions_count,
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
        next_tier = RewardTier.objects.filter(
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
        entries = RewardPointLedger.objects.filter(
            tenant=tenant,
            user=request.user
        ).order_by('-created_at')[:50]

        return Response(RewardPointLedgerSerializer(entries, many=True).data)


class ClientBadgeView(APIView):
    """
    Returns earned and available badges for the authenticated client without N+1 queries.
    """
    permission_classes = [IsAuthenticated, IsRewardClient]

    def get(self, request):
        user = request.user
        tenant = get_request_tenant(request)

        earned_user_badges = UserBadge.objects.filter(
            tenant=tenant,
            user=user
        ).select_related('badge')

        earned_badge_ids = {ub.badge_id for ub in earned_user_badges}

        available_badges = Badge.objects.filter(
            tenant=tenant,
            is_active=True
        ).annotate(awarded_count=Count('user_awards'))

        return Response({
            'earned_badges': UserBadgeSerializer(earned_user_badges, many=True).data,
            'all_badges': BadgeSerializer(available_badges, many=True).data,
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
        streaks = UserStreak.objects.filter(
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

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return RewardCatalogItem.objects.filter(
            tenant=tenant,
            is_active=True
        ).filter(
            Q(stock_quantity__isnull=True) | Q(stock_quantity__gt=0)
        ).select_related('package_type')

    def list(self, request, *args, **kwargs):
        tenant = get_request_tenant(request)
        queryset = self.filter_queryset(self.get_queryset())
        wallet = RewardWalletService.get_or_create_wallet(tenant_id=tenant.id, user=request.user)

        serializer = self.get_serializer(queryset, many=True)
        items_data = serializer.data

        # Add client affordability indicator
        for item in items_data:
            item['can_afford'] = wallet.balance >= item['points_cost']
            item['points_remaining_needed'] = max(0, item['points_cost'] - wallet.balance)

        return Response({
            'wallet_balance': wallet.balance,
            'catalog_items': items_data
        })


class ClientRedemptionViewSet(viewsets.ModelViewSet):
    """
    Member redemption submission and personal voucher history.
    Eagerly joins catalog_item and package_type.
    """
    serializer_class = RewardRedemptionSerializer
    permission_classes = [IsAuthenticated, IsRewardClient]

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return RewardRedemption.objects.filter(
            tenant=tenant,
            user=self.request.user
        ).select_related('catalog_item', 'catalog_item__package_type', 'fulfilled_by')

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
