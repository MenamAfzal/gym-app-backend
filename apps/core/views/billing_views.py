from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.conf import settings

from apps.core.tenants.stripe_service import StripeService
from apps.core.tenants.serializers import CheckoutInitSerializer
from apps.users.models import UserRole

class IsGymOwner(permissions.BasePermission):
    """
    Only Gym Owners can manage the SaaS subscription.
    """
    def has_permission(self, request, view):
        return (
            request.user and 
            request.user.is_authenticated and 
            request.user.role == UserRole.GYM_OWNER
        )

class BillingViewSet(viewsets.ViewSet):
    """
    API for Tenant Billing (Stripe Integration).
    """
    permission_classes = [IsGymOwner]

    @action(detail=False, methods=['post'], url_path='checkout-session')
    def create_checkout_session(self, request):
        """
        Initiates a Stripe Checkout Session for the current tenant.
        Payload: { "plan_id": "...", "success_url": "...", "cancel_url": "..." }
        """
        serializer = CheckoutInitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        plan = serializer.validated_data['plan_id'] # This is the Plan object instance (from validator)
        tenant = request.user.tenant
        
        # Default URLs if not provided (Adjust frontend_url to your env settings)
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
        success_url = serializer.validated_data.get('success_url', f"{frontend_url}/dashboard/billing?status=success")
        cancel_url = serializer.validated_data.get('cancel_url', f"{frontend_url}/dashboard/billing?status=canceled")

        try:
            checkout_url = StripeService.create_checkout_session(
                tenant=tenant,
                plan=plan,
                user_email=request.user.email,
                success_url=success_url,
                cancel_url=cancel_url
            )
            return Response({"checkout_url": checkout_url}, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'], url_path='portal-session')
    def create_portal_session(self, request):
        """
        Generates a link to the Stripe Customer Portal (Update Card / Cancel).
        """
        tenant = request.user.tenant
        frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
        return_url = request.data.get('return_url', f"{frontend_url}/dashboard/billing")

        try:
            portal_url = StripeService.create_portal_session(
                tenant=tenant,
                return_url=return_url
            )
            return Response({"portal_url": portal_url}, status=status.HTTP_200_OK)
            
        except ValueError as e:
            # Likely no stripe_customer_id yet
            return Response(
                {"detail": "No billing account found. Please subscribe first."}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path='referrals')
    def get_referral_rewards(self, request):
        """
        Retrieve referral rewards earned by this gym.
        """
        tenant = request.user.tenant
        if not tenant:
            return Response({"detail": "No active tenant context."}, status=status.HTTP_400_BAD_REQUEST)
            
        from apps.core.tenants.models import ReferralReward
        from apps.core.tenants.serializers import ReferralRewardSerializer
        
        rewards = ReferralReward.objects.filter(referrer=tenant)
        serializer = ReferralRewardSerializer(rewards, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
        