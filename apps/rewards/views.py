"""
Reward Engine Views & ViewSets

Separates Business Admin configuration/fulfillment endpoints from Client-facing
wallet and redemption interactions.
Highly optimized with query annotations and eager joins to eliminate N+1 queries.
"""
from rest_framework import viewsets, status, mixins, parsers
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Sum, Count, Q

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
    RewardWalletService, RewardRedemptionService
)
from apps.users.models import User, UserRole
from apps.core.tenants.context import get_current_tenant


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

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return RewardProgram.objects.filter(tenant=tenant).annotate(rules_count=Count('rules'))

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


class AdminRewardRuleVersionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only view for historical rule versions.
    """
    serializer_class = RewardRuleVersionSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardRuleVersion.objects.filter(rule__tenant=tenant).select_related('created_by')
        
        rule_id = self.request.query_params.get('rule_id')
        if rule_id:
            qs = qs.filter(rule_id=rule_id)
            
        return qs.order_by('-version')


class AdminBadgeViewSet(viewsets.ModelViewSet):
    """
    CRUD management for tenant achievement badges.
    Supports JSON and multipart/form-data for direct badge image uploads.
    Annotates awarded_count to eliminate N+1 queries.
    """
    serializer_class = BadgeSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return Badge.all_objects.filter(tenant=tenant).annotate(awarded_count=Count('awarded_users'))

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
    Eagerly joins package_type and supports multipart image uploads.
    """
    serializer_class = RewardCatalogItemSerializer
    permission_classes = [IsAuthenticated, IsRewardAdminOrManager]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return RewardCatalogItem.all_objects.filter(tenant=tenant).select_related('package_type')

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

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        qs = RewardRedemption.all_objects.filter(tenant=tenant).select_related(
            'user', 'catalog_item', 'catalog_item__package_type', 'fulfilled_by', 'granted_package', 'granted_package__package_type'
        )
        status_param = self.request.query_params.get('status')
        code = self.request.query_params.get('code')

        if status_param:
            qs = qs.filter(status=status_param)
        if code:
            qs = qs.filter(redemption_code__iexact=code.strip())

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

    def get_queryset(self):
        tenant = get_request_tenant(self.request)
        return RewardWallet.objects.filter(tenant=tenant).select_related('user', 'current_tier')

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

        top_members = list(RewardWallet.objects.filter(tenant=tenant).select_related('user').order_by('-lifetime_earned')[:5].values(
            'user__email', 'balance', 'lifetime_earned'
        ))

        successful_rules = list(RewardTransaction.objects.filter(
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

        available_badges = Badge.all_objects.filter(
            tenant=tenant,
            is_active=True
        ).annotate(awarded_count=Count('awarded_users'))

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
        return RewardCatalogItem.all_objects.filter(
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
        return RewardRedemption.all_objects.filter(
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
    Client Referral Management & Testing API.
    Provides members with their referral code/link, and processes referral completions
    by emitting the canonical referral.completed event to the Rewards Engine.
    """
    permission_classes = [IsAuthenticated, IsRewardClient]

    def get(self, request):
        tenant = get_request_tenant(request)
        user = request.user
        code = f"REF-{user.id.hex[:8].upper()}"
        base_url = request.build_absolute_uri('/')[:-1]

        referral_count = ProcessedRewardEvent.objects.filter(
            tenant=tenant,
            event_type='referral.completed',
            payload__referrer_id=str(user.id)
        ).count()

        return Response({
            'referrer_id': str(user.id),
            'referrer_email': user.email,
            'referral_code': code,
            'referral_link': f"{base_url}/join?ref={code}",
            'total_referrals_completed': referral_count,
            'description': "Share your referral code with friends. When they join, you earn reward points and package credits."
        })

    def post(self, request):
        tenant = get_request_tenant(request)
        referrer = request.user

        referee_id = request.data.get('referee_id')
        referee_email = request.data.get('referee_email')
        referral_code = request.data.get('referral_code')

        if not referee_id and not referee_email and not referral_code:
            return Response(
                {'error': 'Either referee_email or referee_id or referral_code is required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        referee = None
        if referee_id:
            try:
                import uuid
                referee = User.objects.filter(id=uuid.UUID(str(referee_id))).first()
            except (ValueError, TypeError):
                return Response({'error': 'Invalid referee_id format.'}, status=status.HTTP_400_BAD_REQUEST)
        elif referee_email:
            referee_email = str(referee_email).strip().lower()
            referee = User.objects.filter(email=referee_email).first()
            if not referee:
                import secrets
                referee = User.objects.create_user(
                    email=referee_email,
                    password=secrets.token_urlsafe(12),
                    tenant=tenant,
                    role=UserRole.CLIENT
                )
        elif referral_code:
            referee = User.objects.filter(id=referrer.id).first()

        if not referee:
            return Response({'error': 'Referee user could not be resolved.'}, status=status.HTTP_404_NOT_FOUND)

        if referee.id == referrer.id:
            return Response({'error': 'Self-referrals are not permitted.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from apps.rewards.events import RewardEvent
            from apps.rewards.services import RewardEngineService

            event = RewardEvent.create_referral_completed(
                tenant_id=tenant.id,
                referrer_id=referrer.id,
                referee_id=referee.id
            )
            transactions = RewardEngineService.handle_event(event)
            points_awarded = sum(
                tx.action_payload.get('amount', 0)
                for tx in transactions
                if tx.action_type == 'POINTS' and tx.result_status == 'SUCCESS'
            )
            return Response({
                'status': 'success',
                'message': 'Referral completed successfully.',
                'referrer_id': str(referrer.id),
                'referrer_email': referrer.email,
                'referee_id': str(referee.id),
                'referee_email': referee.email,
                'event_type': 'referral.completed',
                'transactions_created': len(transactions),
                'points_awarded': points_awarded
            }, status=status.HTTP_201_CREATED)
        except Exception as ex:
            return Response({'error': f"Failed processing referral: {str(ex)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
