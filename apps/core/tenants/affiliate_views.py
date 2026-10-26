"""
Affiliate API Views

Exposes public registration/resolution endpoints, self-service affiliate portal endpoints,
and platform administration endpoints for external B2B affiliates.
"""
from django.http import HttpResponse
from django.conf import settings
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
    Returns distinguishable validation errors (400 Bad Request) for invalid vs inactive codes.
    """
    permission_classes = [permissions.AllowAny]

    def _resolve(self, request, code_value):
        if not code_value:
            return Response({
                'valid': False,
                'error': 'missing_code',
                'detail': 'Parameter "code" or "ref" is required.'
            }, status=status.HTTP_400_BAD_REQUEST)

        clean_code = str(code_value).strip()
        partner = AffiliatePartner.objects.select_related('user').filter(code__iexact=clean_code).first()

        if not partner:
            return Response({
                'valid': False,
                'code': clean_code,
                'error': 'invalid_code',
                'detail': f"The affiliate code '{clean_code}' is invalid."
            }, status=status.HTTP_400_BAD_REQUEST)

        if not partner.is_active:
            return Response({
                'valid': False,
                'code': clean_code,
                'error': 'inactive_code',
                'detail': f"The affiliate code '{clean_code}' is inactive."
            }, status=status.HTTP_400_BAD_REQUEST)

        user = getattr(partner, 'user', None)
        affiliate_name = (
            partner.company_name or 
            (user.get_full_name().strip() if user and user.get_full_name() else None) or 
            (user.email if user else partner.code)
        )

        return Response({
            'valid': True,
            'code': partner.code,
            'company_name': partner.company_name,
            'affiliate_name': affiliate_name,
            'email': user.email if user else None
        }, status=status.HTTP_200_OK)

    def get(self, request):
        code = request.query_params.get('code') or request.query_params.get('ref')
        return self._resolve(request, code)

    def post(self, request):
        code = (
            request.data.get('code') or 
            request.data.get('ref') or 
            request.query_params.get('code') or 
            request.query_params.get('ref')
        )
        return self._resolve(request, code)


class AffiliateJoinLandingView(views.APIView):
    """
    Handles shared affiliate referral links e.g. https://<domain>/join-gym?ref=ALEXFIT
    - If accessed via browser (HTML): Serves an aesthetic, responsive landing page inviting
      the gym owner to onboard with their affiliate code pre-applied.
    - If accessed via API (JSON): Returns partner verification and onboarding target URLs.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request, code=None):
        code = code or request.query_params.get('ref') or request.query_params.get('code') or ''
        clean_code = code.strip().strip('/')

        partner = None
        is_active = False
        error_type = None

        if clean_code:
            partner = AffiliatePartner.objects.select_related('user').filter(code__iexact=clean_code).first()
            if not partner:
                error_type = 'invalid_code'
            elif not partner.is_active:
                error_type = 'inactive_code'
            else:
                is_active = True

        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')
        onboarding_url = f"{frontend_url}/register?ref={partner.code}" if (partner and is_active) else f"{frontend_url}/register"
        registration_url = f"{frontend_url}/onboarding?ref={partner.code}" if (partner and is_active) else f"{frontend_url}/onboarding"

        user = getattr(partner, 'user', None) if partner else None
        affiliate_name = (
            (partner.company_name if partner and partner.company_name else None) or
            (user.get_full_name().strip() if user and user.get_full_name() else None) or
            (user.email if user else None) or
            (partner.code if partner else "")
        )

        accept_header = request.headers.get('Accept', '')
        if 'application/json' in accept_header and 'text/html' not in accept_header:
            return Response({
                'valid': is_active,
                'affiliate_code': partner.code if partner else clean_code,
                'affiliate_name': affiliate_name if is_active else None,
                'company_name': partner.company_name if (partner and is_active) else None,
                'error': error_type,
                'onboarding_url': onboarding_url,
                'registration_url': registration_url,
            }, status=status.HTTP_200_OK if is_active else (status.HTTP_400_BAD_REQUEST if clean_code else status.HTTP_200_OK))

        # HTML Landing Page for Browser
        status_banner = ""
        if is_active:
            status_banner = f"""
            <div id="affiliate-banner" class="banner banner-success">
                <div class="banner-icon">✓</div>
                <div>
                    <div class="banner-title">Verified Partner Invitation</div>
                    <div class="banner-desc">You've been invited by <strong>{affiliate_name}</strong> (Code: <code>{partner.code}</code>) to launch your gym on FitVerx. Referral privileges & bonus onboarding credits will be applied automatically.</div>
                </div>
            </div>
            """
        elif error_type == 'inactive_code':
            status_banner = f"""
            <div id="affiliate-banner" class="banner banner-warning">
                <div class="banner-icon">!</div>
                <div>
                    <div class="banner-title">Inactive Affiliate Code</div>
                    <div class="banner-desc">The partner code <code>{clean_code}</code> is currently inactive. You can still proceed with gym registration directly.</div>
                </div>
            </div>
            """
        elif clean_code:
            status_banner = f"""
            <div id="affiliate-banner" class="banner banner-warning">
                <div class="banner-icon">!</div>
                <div>
                    <div class="banner-title">Partner Code Not Found</div>
                    <div class="banner-desc">The code <code>{clean_code}</code> was not recognized. You can still create and launch your gym directly.</div>
                </div>
            </div>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FitVerx — Gym Partner Onboarding Invitation</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-base: #0B0F19;
            --bg-card: rgba(17, 24, 39, 0.85);
            --border-card: rgba(255, 255, 255, 0.08);
            --accent: #6366F1;
            --accent-glow: rgba(99, 102, 241, 0.25);
            --accent-hover: #4F46E5;
            --text-main: #F9FAFB;
            --text-muted: #9CA3AF;
            --success: #10B981;
            --warning: #F59E0B;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Outfit', sans-serif;
            background: radial-gradient(circle at 50% 0%, #1E1B4B 0%, var(--bg-base) 70%);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 30px 20px;
        }}
        .container {{
            max-width: 680px;
            width: 100%;
            background: var(--bg-card);
            border: 1px solid var(--border-card);
            backdrop-filter: blur(16px);
            border-radius: 20px;
            padding: 44px 36px;
            box-shadow: 0 25px 60px -15px rgba(0, 0, 0, 0.6), 0 0 40px var(--accent-glow);
        }}
        .brand-header {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 24px;
        }}
        .brand-logo {{
            width: 44px;
            height: 44px;
            border-radius: 12px;
            background: linear-gradient(135deg, #6366F1 0%, #A855F7 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 22px;
            color: #fff;
            box-shadow: 0 4px 15px rgba(99, 102, 241, 0.4);
        }}
        .brand-name {{
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.5px;
        }}
        h1 {{
            font-size: 32px;
            font-weight: 700;
            line-height: 1.25;
            margin-bottom: 12px;
            background: linear-gradient(135deg, #FFFFFF 0%, #CBD5E1 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        .subtitle {{
            font-size: 16px;
            color: var(--text-muted);
            line-height: 1.6;
            margin-bottom: 28px;
        }}
        .banner {{
            border-radius: 12px;
            padding: 16px 18px;
            margin-bottom: 28px;
            display: flex;
            gap: 14px;
            align-items: flex-start;
            font-size: 14px;
            line-height: 1.5;
        }}
        .banner-success {{
            background: rgba(16, 185, 129, 0.12);
            border: 1px solid rgba(16, 185, 129, 0.3);
            color: #D1FAE5;
        }}
        .banner-warning {{
            background: rgba(245, 158, 11, 0.12);
            border: 1px solid rgba(245, 158, 11, 0.3);
            color: #FEF3C7;
        }}
        .banner-icon {{
            width: 24px;
            height: 24px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            font-size: 13px;
            flex-shrink: 0;
            background: currentColor;
            color: #0B0F19;
        }}
        .banner-title {{
            font-weight: 600;
            margin-bottom: 2px;
        }}
        .banner-desc code {{
            background: rgba(0, 0, 0, 0.3);
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
            font-size: 13px;
        }}
        .features-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            margin-bottom: 32px;
        }}
        .feature-card {{
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 12px;
            padding: 16px;
            transition: transform 0.2s, border-color 0.2s;
        }}
        .feature-card:hover {{
            transform: translateY(-2px);
            border-color: rgba(99, 102, 241, 0.3);
        }}
        .feature-title {{
            font-weight: 600;
            font-size: 14px;
            margin-bottom: 4px;
            color: #E2E8F0;
        }}
        .feature-desc {{
            font-size: 12px;
            color: var(--text-muted);
            line-height: 1.4;
        }}
        .actions {{
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .btn {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            width: 100%;
            padding: 15px 24px;
            border-radius: 12px;
            font-size: 16px;
            font-weight: 600;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.2s ease;
            border: none;
        }}
        .btn-primary {{
            background: linear-gradient(135deg, #6366F1 0%, #4F46E5 100%);
            color: #FFFFFF;
            box-shadow: 0 10px 25px -5px rgba(99, 102, 241, 0.4);
        }}
        .btn-primary:hover {{
            transform: translateY(-2px);
            box-shadow: 0 15px 30px -5px rgba(99, 102, 241, 0.5);
        }}
        .btn-secondary {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            color: var(--text-main);
        }}
        .btn-secondary:hover {{
            background: rgba(255, 255, 255, 0.08);
        }}
        .footer-note {{
            margin-top: 24px;
            text-align: center;
            font-size: 12px;
            color: var(--text-muted);
        }}
        @media (max-width: 600px) {{
            .features-grid {{ grid-template-columns: 1fr; }}
            .container {{ padding: 28px 20px; }}
            h1 {{ font-size: 26px; }}
        }}
    </style>
</head>
<body>
    <main class="container">
        <header class="brand-header">
            <div class="brand-logo">F</div>
            <span class="brand-name">FitVerx Cloud</span>
        </header>

        <h1>Empower Your Gym Business</h1>
        <p class="subtitle">Complete fitness management, seamless member scheduling, automated billing, and AI-driven retention tailored for modern gyms.</p>

        {status_banner}

        <section class="features-grid" aria-label="Platform Highlights">
            <article class="feature-card">
                <div class="feature-title">Member App & Check-In</div>
                <div class="feature-desc">Branded mobile client experiences, barcode scan, and workout tracking.</div>
            </article>
            <article class="feature-card">
                <div class="feature-title">Automated Stripe Billing</div>
                <div class="feature-desc">Recurring gym memberships, package credits, and self-service portals.</div>
            </article>
            <article class="feature-card">
                <div class="feature-title">Smart Class Scheduling</div>
                <div class="feature-desc">Real-time trainer assignments, capacity limits, and waitlists.</div>
            </article>
            <article class="feature-card">
                <div class="feature-title">Gamified Retention Engine</div>
                <div class="feature-desc">Streaks, reward points, badges, and automated re-engagement triggers.</div>
            </article>
        </section>

        <section class="actions">
            <a id="btn-register" class="btn btn-primary" href="{onboarding_url}">
                Get Started & Register Gym →
            </a>
            <a id="btn-portal" class="btn btn-secondary" href="{frontend_url}/login">
                Already have an account? Sign In
            </a>
        </section>

        <footer class="footer-note">
            Referral partner program powered by FitVerx B2B Affiliate Network.
        </footer>
    </main>
</body>
</html>"""
        return HttpResponse(html_content, content_type='text/html')


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
