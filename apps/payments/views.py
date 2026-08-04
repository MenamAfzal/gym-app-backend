import json
import stripe
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import logging
from apps.core.tenants.models import Tenant
from .models import TenantSubscription, FeatureToggle, PlatformLedger, TenantPayout
from .services import LedgerService

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY
stripe_webhook_secret = settings.STRIPE_WEBHOOK_SECRET

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, viewsets
from apps.users.models import UserRole
from .serializers import PlatformLedgerSerializer, TenantPayoutSerializer, TenantSubscriptionSerializer, FeatureToggleSerializer

class TenantLedgerViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Allows Gym Owners to see their own payout and fee ledgers.
    Because of TenantAwareManager, this automatically filters to the current tenant.
    """
    serializer_class = PlatformLedgerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role not in [UserRole.GYM_OWNER, UserRole.PLATFORM_ADMIN]:
            return PlatformLedger.objects.none()
        return PlatformLedger.objects.all()

class PlatformLedgerViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Allows Platform Admins to see ledgers across all tenants.
    Bypasses tenant isolation for cross-tenant visibility.
    """
    serializer_class = PlatformLedgerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role != UserRole.PLATFORM_ADMIN:
            return PlatformLedger.objects.none()
            
        from apps.core.tenants.context import bypass_tenant_isolation
        with bypass_tenant_isolation():
            # Use all_objects to bypass tenant isolation
            return PlatformLedger.all_objects.all()

class StripeConnectOnboardingView(APIView):
    """
    Endpoint for Gym Owners to initiate Stripe Connect Onboarding.
    This creates an Express account, saves the ID, and returns the onboarding URL.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        
        # Only allow Gym Owners to setup payouts
        if user.role != UserRole.GYM_OWNER:
            return Response({"error": "Only Gym Owners can set up payouts."}, status=status.HTTP_403_FORBIDDEN)
            
        tenant = user.tenant
        if not tenant:
            return Response({"error": "No tenant associated with this user."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Step 1: Check if they already have an account ID saved. If not, create one.
            if not tenant.stripe_account_id:
                account = stripe.Account.create(
                    type="express",
                    country="US", # This could be dynamic based on your app's needs
                    email=user.email,
                    capabilities={
                        "card_payments": {"requested": True},
                        "transfers": {"requested": True},
                    },
                )
                
                # Save the new ID directly to the Tenant model!
                tenant.stripe_account_id = account.id
                tenant.save()
            
            # Step 2: Generate the onboarding link
            # You should configure these URLs in your frontend
            return_url = f"https://gym-owner-portal-iota.vercel.app/dashboard/finance?success=true"
            refresh_url = f"https://gym-owner-portal-iota.vercel.app/dashboard/finance?refresh=true"
            
            account_link = stripe.AccountLink.create(
                account=tenant.stripe_account_id,
                refresh_url=refresh_url,
                return_url=return_url,
                type="account_onboarding",
            )
            
            # Return the URL so the frontend can redirect the user
            return Response({"url": account_link.url}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Stripe Connect error for tenant {tenant.id}: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """
    Webhook handler for Stripe events.
    """
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    event = None

    try:
        # Avoid verifying signature if secret is a mock string during dev tests
        if stripe_webhook_secret == 'whsec_mock':
            event = json.loads(payload)
        else:
            event = stripe.Webhook.construct_event(
                payload, sig_header, stripe_webhook_secret
            )
    except ValueError as e:
        # Invalid payload
        logger.error(f"Invalid payload: {str(e)}")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        logger.error(f"Invalid signature: {str(e)}")
        return HttpResponse(status=400)
    except Exception as e:
        logger.error(f"Webhook processing error: {str(e)}")
        return HttpResponse(status=400)

    # Handle the event
    event_type = event.get('type')
    data_object = event.get('data', {}).get('object', {})

    logger.info(f"Received Stripe webhook event: {event_type}")

    if event_type == 'customer.subscription.updated':
        _handle_subscription_updated(data_object)
    elif event_type == 'customer.subscription.deleted':
        _handle_subscription_deleted(data_object)
    elif event_type == 'invoice.payment_succeeded':
        _handle_invoice_payment_succeeded(data_object)
    else:
        logger.info(f"Unhandled Stripe event type: {event_type}")

    return HttpResponse(status=200)

def _handle_subscription_updated(subscription_obj):
    stripe_sub_id = subscription_obj.get('id')
    status = subscription_obj.get('status')
    # Optional logic to update local TenantSubscription
    try:
        sub = TenantSubscription.objects.get(stripe_subscription_id=stripe_sub_id)
        sub.status = status
        sub.save()
        logger.info(f"Updated subscription {stripe_sub_id} status to {status}")
    except TenantSubscription.DoesNotExist:
        logger.warning(f"Subscription {stripe_sub_id} not found in local DB.")

def _handle_subscription_deleted(subscription_obj):
    stripe_sub_id = subscription_obj.get('id')
    try:
        sub = TenantSubscription.objects.get(stripe_subscription_id=stripe_sub_id)
        sub.status = TenantSubscription.StatusChoices.CANCELED
        sub.save()
        logger.info(f"Canceled subscription {stripe_sub_id}")
    except TenantSubscription.DoesNotExist:
        pass

def _handle_invoice_payment_succeeded(invoice_obj):
    # Depending on the billing architecture, you might record this as a ledger charge,
    invoice_id = invoice_obj.get('id')
    amount_paid = invoice_obj.get('amount_paid', 0)
    logger.info(f"Invoice {invoice_id} paid for amount {amount_paid}")
