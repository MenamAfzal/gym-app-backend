"""
Affiliate API Views

Exposes public registration/resolution endpoints, self-service affiliate portal endpoints,
and platform administration endpoints for external B2B affiliates.
"""
from rest_framework import views, viewsets, permissions, status, generics, filters
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework_simplejwt.tokens import RefreshToken
from django.core.exceptions import ValidationError as DjangoValidationError

from apps.users.models import UserRole
from apps.core.views.platform_views import IsPlatformAdmin
from apps.core.tenants.models import AffiliatePartner, AffiliatePointsTransaction, Tenant
from apps.core.tenants.affiliate_service import AffiliateService
from apps.core.tenants.serializers import (
    AffiliateRegistrationSerializer,
    AffiliatePartnerSerializer,
    AffiliatePointsTransactionSerializer,
    AffiliateReferredGymSerializer,
    AffiliatePointsAdjustmentSerializer
)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class IsAffiliate(permissions.BasePermission):
    """
    Permission check: Authenticated user must have role=AFFILIATE and an active AffiliatePartner profile.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            getattr(request.user, 'role', None) == UserRole.AFFILIATE and
            hasattr(request.user, 'affiliate_partner') and
            request.user.affiliate_partner.is_active
        )


class AffiliateRegisterView(views.APIView):
    """
    Public endpoint: Allows an external promoter/partner to register as an affiliate.
    Does not require or create a gym tenant account.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = AffiliateRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            user, partner = AffiliateService.register_affiliate(
                email=data['email'],
                password=data['password'],
                first_name=data.get('first_name', ''),
                last_name=data.get('last_name', ''),
                code=data.get('code'),
                company_name=data.get('company_name', ''),
                website_or_social=data.get('website_or_social', '')
            )
 
            refresh = RefreshToken.for_user(user)
            base_url = request.build_absolute_uri('/')[:-1]

            return Response({
                'message': 'Affiliate partner registered successfully.',
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                },
                'affiliate': AffiliatePartnerSerializer(partner).data,
                'referral_link': f"{base_url}/join-gym?ref={partner.code}"
            }, status=status.HTTP_201_CREATED)

        except DjangoValidationError as e:
            msg = e.message if hasattr(e, 'message') else str(e)
            return Response({'detail': msg, 'error': msg}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class AffiliateResolveView(views.APIView):
    """
    Public endpoint: Validates an affiliate code before or during gym registration.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        code = request.query_params.get('code') or request.query_params.get('ref')
        if not code:
            return Response({'error': 'Parameter "code" or "ref" is required.'}, status=status.HTTP_400_BAD_REQUEST)

        partner = AffiliateService.resolve_affiliate(code)
        if not partner:
            return Response({
                'valid': False,
                'error': 'Invalid or inactive affiliate code.'
            }, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'valid': True,
            'code': partner.code,
            'company_name': partner.company_name,
            'affiliate_name': partner.user.get_full_name() or partner.user.email
        }, status=status.HTTP_200_OK)


class AffiliateDashboardView(views.APIView):
    """
    Self-service portal: Retrieves aggregate dashboard stats, referral link, and recent transactions.
    """
    permission_classes = [IsAffiliate]

    def get(self, request):
        partner = request.user.affiliate_partner
        partner.refresh_from_db()
        base_url = request.build_absolute_uri('/')[:-1]
        data = AffiliateService.get_affiliate_dashboard_data(partner, base_url=base_url)
        return Response(data, status=status.HTTP_200_OK)


class AffiliateHistoryView(generics.ListAPIView):
    """
    Self-service portal: Paginated audit ledger of points transactions for the logged-in affiliate.
    """
    permission_classes = [IsAffiliate]
    serializer_class = AffiliatePointsTransactionSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        partner = self.request.user.affiliate_partner
        return partner.point_transactions.all().select_related('tenant', 'subscription')


class AffiliateReferredGymsView(generics.ListAPIView):
    """
    Self-service portal: Paginated list of gym tenants referred by the logged-in affiliate.
    """
    permission_classes = [IsAffiliate]
    serializer_class = AffiliateReferredGymSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        partner = self.request.user.affiliate_partner
        return Tenant.objects.filter(affiliate=partner).prefetch_related('subscriptions', 'subscriptions__plan').order_by('-created_at')


class PlatformAffiliateViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Platform Admin viewset for auditing and managing all external B2B affiliates.
    """
    permission_classes = [IsPlatformAdmin]
    serializer_class = AffiliatePartnerSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['code', 'company_name', 'user__email', 'user__first_name', 'user__last_name']
    ordering_fields = ['points_balance', 'lifetime_points_earned', 'created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        return AffiliatePartner.objects.all().select_related('user').prefetch_related('referred_tenants')

    @action(detail=True, methods=['post'], url_path='adjust-points')
    def adjust_points(self, request, pk=None):
        affiliate = self.get_object()
        serializer = AffiliatePointsAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        points = serializer.validated_data['points']
        reason = serializer.validated_data['reason']

        try:
            tx = AffiliateService.adjust_affiliate_points(
                affiliate=affiliate,
                points=points,
                reason=reason,
                admin_user=request.user
            )
            affiliate.refresh_from_db()
            return Response({
                'message': f"Adjusted {points} points for affiliate '{affiliate.code}'.",
                'points_balance': affiliate.points_balance,
                'lifetime_points_earned': affiliate.lifetime_points_earned,
                'transaction': AffiliatePointsTransactionSerializer(tx).data
            }, status=status.HTTP_200_OK)
        except DjangoValidationError as e:
            msg = e.message if hasattr(e, 'message') else str(e)
            return Response({'detail': msg}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
