import json
import stripe
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import logging
from apps.core.tenants.models import Tenant
from .models import (
    TenantSubscription,
    FeatureToggle,
    PlatformLedger,
    TenantPayout,
    BillingFeature,
    BillingPlan,
    TenantBillingSubscription,
)
from .services import LedgerService
from .billing_service import BillingValidationError, FeatureBillingService
from .billing_serializers import (
    BillingFeatureSerializer,
    BillingPlanSerializer,
    CheckoutRequestSerializer,
    TenantBillingSubscriptionSerializer,
)

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


class TriggerPayoutView(APIView):
    """
    Endpoint for Platform Admins to manually trigger the payout aggregation job.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        
        # Only allow Platform Admins to trigger payouts
        if user.role != UserRole.PLATFORM_ADMIN:
            return Response({"error": "Only Platform Admins can trigger payouts."}, status=status.HTTP_403_FORBIDDEN)

        try:
            from .tasks import process_scheduled_payouts
            payouts_created = process_scheduled_payouts()
            return Response({
                "detail": f"Payout process triggered successfully. Created {payouts_created} payouts."
            }, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error triggering payouts manually: {str(e)}")
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
        # Also sync the new TenantBillingSubscription status
        FeatureBillingService.handle_subscription_updated(data_object)
    elif event_type == 'customer.subscription.deleted':
        _handle_subscription_deleted(data_object)
        FeatureBillingService.handle_subscription_deleted(data_object)
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
    # Depending on the billing architecture, you might record this as a ledger charge.
    invoice_id = invoice_obj.get('id')
    amount_paid = invoice_obj.get('amount_paid', 0)
    logger.info(f"Invoice {invoice_id} paid for amount {amount_paid}")


# =============================================================================
# Feature-Based Billing Views
# =============================================================================

class BillingFeatureListView(APIView):
    """
    GET /api/v1/payments/billing/features/

    Returns all active premium features available for purchase.
    Used by the frontend to populate the plan-selection dropdown / checklist.
    stripe_price_id is intentionally excluded from the response.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        features = BillingFeature.objects.filter(is_active=True).order_by('name')
        serializer = BillingFeatureSerializer(features, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BillingPlanListView(APIView):
    """
    GET /api/v1/payments/billing/plans/

    Returns all publicly available plan tiers and their feature constraints.
    Used by the frontend to render the pricing/upgrade UI.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        plans = BillingPlan.objects.filter(is_public=True).order_by('name')
        serializer = BillingPlanSerializer(plans, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class TenantBillingSubscriptionView(APIView):
    """
    GET /api/v1/payments/billing/subscription/

    Returns the current active TenantBillingSubscription for the requesting
    gym owner's tenant, including the unlocked features.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        tenant = getattr(user, 'tenant', None)
        if not tenant:
            return Response(
                {"error": "No tenant associated with your account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Fetch most recent active sub; fall back to any latest sub
        billing_sub = (
            TenantBillingSubscription.objects
            .filter(tenant=tenant)
            .select_related('billing_plan')
            .prefetch_related('active_features')
            .order_by(
                # Active first, then by recency
                '-status',
                '-created_at',
            )
            .first()
        )

        if not billing_sub:
            return Response(
                {"detail": "No billing subscription found for this tenant."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = TenantBillingSubscriptionSerializer(billing_sub)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CreateCheckoutSessionView(APIView):
    """
    POST /api/v1/payments/billing/checkout/

    Initiates a Stripe Checkout Session for a feature-based subscription upgrade.

    Request body
    ------------
    {
        "plan": "basic" | "premium" | "custom",
        "feature_ids": ["<uuid>", "<uuid>", ...]   // required for basic/custom
    }

    Response (200)
    --------------
    {
        "checkout_url": "https://checkout.stripe.com/pay/cs_...",
        "session_id": "cs_..."
    }

    Errors
    ------
    400  – Validation failure (wrong feature count, unknown plan, etc.)
    403  – Caller is not a Gym Owner
    502  – Stripe API error
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        # Only Gym Owners may initiate a checkout
        if user.role not in [UserRole.GYM_OWNER, UserRole.PLATFORM_ADMIN]:
            return Response(
                {"error": "Only Gym Owners can initiate a subscription checkout."},
                status=status.HTTP_403_FORBIDDEN,
            )

        tenant = getattr(user, 'tenant', None)
        if not tenant:
            return Response(
                {"error": "No tenant is associated with your account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate request body ---------------------------------------------------
        serializer = CheckoutRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        plan_slug = serializer.validated_data['plan']
        feature_ids = serializer.validated_data['feature_ids']

        # Delegate to service layer -----------------------------------------------
        try:
            result = FeatureBillingService.create_checkout_session(
                tenant=tenant,
                plan_slug=plan_slug,
                feature_ids=feature_ids,
                customer_email=user.email,
            )
        except BillingValidationError as exc:
            return Response(
                {"error": str(exc)},
                status=getattr(exc, 'status_code', status.HTTP_400_BAD_REQUEST),
            )
        except stripe.error.StripeError as exc:
            logger.error(
                "Stripe error during checkout for tenant %s: %s",
                tenant.id, str(exc),
            )
            return Response(
                {"error": "Payment provider error. Please try again later."},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:
            logger.exception(
                "Unexpected error during checkout for tenant %s: %s",
                tenant.id, str(exc),
            )
            return Response(
                {"error": "An unexpected error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(result, status=status.HTTP_200_OK)


@csrf_exempt
@require_POST
def stripe_checkout_webhook(request):
    """
    POST /api/v1/payments/webhook/stripe/checkout/

    Dedicated webhook endpoint for ``checkout.session.completed`` events.
    Verifies the Stripe-Signature header and delegates to
    ``FeatureBillingService.fulfill_checkout()`` to upgrade the tenant's
    subscription atomically.

    Security
    --------
    - Signature verification is enforced in production (STRIPE_WEBHOOK_SECRET
      must be set to a real whsec_ value).
    - In development, if STRIPE_WEBHOOK_SECRET == 'whsec_mock', signature
      verification is skipped so you can use the Stripe CLI / test events.
    """
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    event = None

    try:
        if stripe_webhook_secret == 'whsec_mock':
            # Development / test mode: skip signature verification
            event = json.loads(payload)
        else:
            event = stripe.Webhook.construct_event(
                payload, sig_header, stripe_webhook_secret
            )
    except ValueError:
        logger.error("Checkout webhook: invalid payload (not valid JSON).")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        logger.error("Checkout webhook: invalid Stripe signature.")
        return HttpResponse(status=400)
    except Exception as exc:
        logger.exception("Checkout webhook: unexpected parse error: %s", exc)
        return HttpResponse(status=400)

    event_type = event.get('type') if isinstance(event, dict) else event.get('type')
    data_object = (
        event.get('data', {}).get('object', {})
        if isinstance(event, dict)
        else event['data']['object']
    )

    logger.info("Checkout webhook received event: %s", event_type)

    if event_type == 'checkout.session.completed':
        try:
            billing_sub = FeatureBillingService.fulfill_checkout(data_object)
            logger.info(
                "checkout.session.completed fulfilled: sub_id=%s tenant=%s",
                billing_sub.stripe_subscription_id,
                billing_sub.tenant_id,
            )
        except ValueError as exc:
            # Metadata / DB lookup errors -- log and return 200 to prevent Stripe
            # from retrying (the error is non-recoverable without data fix).
            logger.error("Checkout fulfillment error: %s", str(exc))
        except Exception as exc:
            logger.exception("Unexpected error fulfilling checkout: %s", exc)
            # Return 500 so Stripe retries (transient DB / network error)
            return HttpResponse(status=500)
    else:
        logger.info("Checkout webhook: unhandled event type '%s'", event_type)

    return HttpResponse(status=200)
