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
stripe_checkout_webhook_secret = getattr(settings, 'STRIPE_CHECKOUT_WEBHOOK_SECRET', stripe_webhook_secret)

from rest_framework.views import APIView
from rest_framework import generics
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
            
        queryset = PlatformLedger.objects.all()
        
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        tx_type = self.request.query_params.get('type')
        status_param = self.request.query_params.get('status')
        
        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)
        if tx_type:
            queryset = queryset.filter(type=tx_type)
        if status_param:
            queryset = queryset.filter(status=status_param)
            
        return queryset.order_by('-created_at')

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
            queryset = PlatformLedger.all_objects.all()
            
            tenant_id = self.request.query_params.get('tenant') or self.request.query_params.get('tenant_id')
            start_date = self.request.query_params.get('start_date')
            end_date = self.request.query_params.get('end_date')
            tx_type = self.request.query_params.get('type')
            status_param = self.request.query_params.get('status')
            
            if tenant_id:
                queryset = queryset.filter(tenant_id=tenant_id)
            if start_date:
                queryset = queryset.filter(created_at__gte=start_date)
            if end_date:
                queryset = queryset.filter(created_at__lte=end_date)
            if tx_type:
                queryset = queryset.filter(type=tx_type)
            if status_param:
                queryset = queryset.filter(status=status_param)
                
            return queryset.order_by('-created_at')

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

    if isinstance(event, dict):
        event_type = event.get('type')
        data_object = event.get('data', {}).get('object', {})
    else:
        event_type = event.type
        data_object = event.data.object

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
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        if hasattr(obj, key):
            return getattr(obj, key)
        try:
            return obj[key]
        except (KeyError, TypeError, Exception):
            pass
        return default

    stripe_sub_id = _get(subscription_obj, 'id')
    status = _get(subscription_obj, 'status')
    # Optional logic to update local TenantSubscription
    try:
        sub = TenantSubscription.objects.get(stripe_subscription_id=stripe_sub_id)
        sub.status = status
        sub.save()
        logger.info(f"Updated subscription {stripe_sub_id} status to {status}")
    except TenantSubscription.DoesNotExist:
        logger.warning(f"Subscription {stripe_sub_id} not found in local DB.")

def _handle_subscription_deleted(subscription_obj):
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        if hasattr(obj, key):
            return getattr(obj, key)
        try:
            return obj[key]
        except (KeyError, TypeError, Exception):
            pass
        return default

    stripe_sub_id = _get(subscription_obj, 'id')
    try:
        sub = TenantSubscription.objects.get(stripe_subscription_id=stripe_sub_id)
        sub.status = TenantSubscription.StatusChoices.CANCELED
        sub.save()
        logger.info(f"Canceled subscription {stripe_sub_id}")
    except TenantSubscription.DoesNotExist:
        pass

def _handle_invoice_payment_succeeded(invoice_obj):
    # Depending on the billing architecture, you might record this as a ledger charge.
    def _get(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        if hasattr(obj, key):
            return getattr(obj, key)
        try:
            return obj[key]
        except (KeyError, TypeError, Exception):
            pass
        return default

    invoice_id = _get(invoice_obj, 'id')
    amount_paid = _get(invoice_obj, 'amount_paid', 0)
    logger.info(f"Invoice {invoice_id} paid for amount {amount_paid}")


class BillingFeatureListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        is_platform_admin = (
            request.user.is_staff and 
            getattr(request.user, 'tenant', None) is None
        )
        if is_platform_admin:
            features = BillingFeature.objects.all().order_by('name')
        else:
            features = BillingFeature.objects.filter(is_active=True).order_by('name')
        serializer = BillingFeatureSerializer(features, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        is_platform_admin = (
            request.user.is_staff and 
            getattr(request.user, 'tenant', None) is None
        )
        if not is_platform_admin:
            return Response({"detail": "Only platform administrators can create features."}, status=status.HTTP_403_FORBIDDEN)
            
        serializer = BillingFeatureSerializer(data=request.data)
        if serializer.is_valid():
            feature = serializer.save()
            try:
                FeatureBillingService.sync_feature_to_stripe(feature)
                feature.save()
            except Exception as e:
                feature.delete()
                return Response({"error": f"Stripe integration failed: {str(e)}"}, status=status.HTTP_502_BAD_GATEWAY)
                
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class BillingFeatureDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        try:
            return BillingFeature.objects.get(pk=pk)
        except BillingFeature.DoesNotExist:
            return None

    def get(self, request, pk):
        feature = self.get_object(pk)
        if not feature:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = BillingFeatureSerializer(feature)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        is_platform_admin = (
            request.user.is_staff and 
            getattr(request.user, 'tenant', None) is None
        )
        if not is_platform_admin:
            return Response({"detail": "Only platform administrators can edit features."}, status=status.HTTP_403_FORBIDDEN)
            
        feature = self.get_object(pk)
        if not feature:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            
        old_price = feature.price
        old_cycle = feature.billing_cycle
        
        serializer = BillingFeatureSerializer(feature, data=request.data, partial=False)
        if serializer.is_valid():
            updated_feature = serializer.save()
            try:
                FeatureBillingService.sync_feature_to_stripe(
                    updated_feature,
                    old_price=old_price,
                    old_cycle=old_cycle
                )
                updated_feature.save()
            except Exception as e:
                return Response({"error": f"Stripe update failed: {str(e)}"}, status=status.HTTP_502_BAD_GATEWAY)
                
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request, pk):
        is_platform_admin = (
            request.user.is_staff and 
            getattr(request.user, 'tenant', None) is None
        )
        if not is_platform_admin:
            return Response({"detail": "Only platform administrators can edit features."}, status=status.HTTP_403_FORBIDDEN)
            
        feature = self.get_object(pk)
        if not feature:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            
        old_price = feature.price
        old_cycle = feature.billing_cycle
        
        serializer = BillingFeatureSerializer(feature, data=request.data, partial=True)
        if serializer.is_valid():
            updated_feature = serializer.save()
            try:
                FeatureBillingService.sync_feature_to_stripe(
                    updated_feature,
                    old_price=old_price,
                    old_cycle=old_cycle
                )
                updated_feature.save()
            except Exception as e:
                return Response({"error": f"Stripe update failed: {str(e)}"}, status=status.HTTP_502_BAD_GATEWAY)
                
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        is_platform_admin = (
            request.user.is_staff and 
            getattr(request.user, 'tenant', None) is None
        )
        if not is_platform_admin:
            return Response({"detail": "Only platform administrators can delete features."}, status=status.HTTP_403_FORBIDDEN)
            
        feature = self.get_object(pk)
        if not feature:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            
        try:
            FeatureBillingService.delete_feature_from_stripe(feature)
            feature.delete()
        except Exception as e:
            return Response({"error": f"Stripe delete failed: {str(e)}"}, status=status.HTTP_502_BAD_GATEWAY)
            
        return Response(status=status.HTTP_204_NO_CONTENT)


class BillingPlanListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        plans = BillingPlan.objects.filter(is_public=True).order_by('name')
        serializer = BillingPlanSerializer(plans, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class TenantBillingSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        tenant = getattr(user, 'tenant', None)
        if not tenant:
            return Response(
                {"error": "No tenant associated with your account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from django.db.models import Case, When, Value, IntegerField
        
        billing_sub = (
            TenantBillingSubscription.objects
            .filter(tenant=tenant)
            .select_related('billing_plan')
            .prefetch_related('active_features')
            .annotate(
                is_active_sort=Case(
                    When(status=TenantBillingSubscription.StatusChoices.ACTIVE, then=Value(1)),
                    default=Value(0),
                    output_field=IntegerField()
                )
            )
            .order_by('-is_active_sort', '-created_at')
            .first()
        )

        if not billing_sub:
            return Response(
                {"detail": "No billing subscription found for this tenant."},
                status=status.HTTP_404_NOT_FOUND,
            )

        active_subs = TenantBillingSubscription.objects.filter(
            tenant=tenant,
            status=TenantBillingSubscription.StatusChoices.ACTIVE
        ).prefetch_related('active_features')
        
        all_features = set()
        for sub in active_subs:
            for feat in sub.active_features.all():
                all_features.add(feat)

        serializer = TenantBillingSubscriptionSerializer(billing_sub)
        data = serializer.data
        
        from .billing_serializers import BillingFeatureSerializer
        data["active_features"] = BillingFeatureSerializer(list(all_features), many=True).data
        return Response(data, status=status.HTTP_200_OK)


class CreateCheckoutSessionView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

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

        serializer = CheckoutRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        plan_slug = serializer.validated_data['plan']
        feature_ids = serializer.validated_data['feature_ids']

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
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE')
    event = None

    try:
        if stripe_checkout_webhook_secret == 'whsec_mock':
            event = json.loads(payload)
        else:
            event = stripe.Webhook.construct_event(
                payload, sig_header, stripe_checkout_webhook_secret
            )
    except ValueError:
        logger.error("Checkout webhook: invalid payload.")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError:
        logger.error("Checkout webhook: invalid Stripe signature.")
        return HttpResponse(status=400)
    except Exception as exc:
        logger.exception("Checkout webhook: unexpected parse error: %s", exc)
        return HttpResponse(status=400)

    if isinstance(event, dict):
        event_type = event.get('type')
        data_object = event.get('data', {}).get('object', {})
    else:
        event_type = event.type
        data_object = event.data.object

    logger.info("Checkout webhook received event: %s", event_type)

    if event_type == 'checkout.session.completed':
        session_dict = data_object if isinstance(data_object, dict) else data_object.to_dict()
        metadata = session_dict.get('metadata', {})
        if metadata.get('type') == 'package_purchase':
            try:
                from apps.payments.stripe_package_service import StripePackageService
                StripePackageService.handle_checkout_session_completed(session_dict)
            except Exception as exc:
                logger.exception("Error handling package checkout session: %s", exc)
        else:
            try:
                billing_sub = FeatureBillingService.fulfill_checkout(session_dict)
                logger.info(
                    "checkout.session.completed fulfilled: sub_id=%s tenant=%s",
                    billing_sub.stripe_subscription_id,
                    billing_sub.tenant_id,
                )
            except ValueError as exc:
                logger.error("Checkout fulfillment error: %s", str(exc))
            except Exception as exc:
                logger.exception("Unexpected error fulfilling checkout: %s", exc)
                return HttpResponse(status=500)
    elif event_type == 'customer.subscription.updated':
        try:
            FeatureBillingService.handle_subscription_updated(data_object)
            from apps.payments.stripe_package_service import StripePackageService
            StripePackageService.handle_subscription_updated(data_object)
            logger.info("customer.subscription.updated handled in checkout webhook.")
        except Exception as exc:
            logger.exception("Error handling subscription.updated in checkout webhook: %s", exc)
    elif event_type == 'customer.subscription.deleted':
        try:
            FeatureBillingService.handle_subscription_deleted(data_object)
            from apps.payments.stripe_package_service import StripePackageService
            StripePackageService.handle_subscription_deleted(data_object)
            logger.info("customer.subscription.deleted handled in checkout webhook.")
        except Exception as exc:
            logger.exception("Error handling subscription.deleted in checkout webhook: %s", exc)
    else:
        logger.info("Checkout webhook: unhandled event type '%s'", event_type)

    return HttpResponse(status=200)

class PackageCheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if user.role != UserRole.CLIENT:
            return Response({"error": "Only clients can purchase packages."}, status=status.HTTP_403_FORBIDDEN)

        package_type_id = request.data.get('package_type_id')
        if not package_type_id:
            return Response({"error": "package_type_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        from apps.scheduling.models import PackageType
        try:
            package_type = PackageType.objects.get(id=package_type_id, tenant=user.tenant)
        except PackageType.DoesNotExist:
            return Response({"error": "Package not found."}, status=status.HTTP_404_NOT_FOUND)

        if not package_type.is_active or not package_type.stripe_price_id:
            return Response({"error": "This package is not available for purchase."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            success_url = request.data.get('success_url', 'https://fit-plus-gym-portal.vercel.app/dashboard/billing/success')
            cancel_url = request.data.get('cancel_url', 'https://fit-plus-gym-portal.vercel.app/dashboard/billing/cancel')

            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price': package_type.stripe_price_id,
                    'quantity': 1,
                }],
                mode='subscription',
                customer_email=user.email,
                metadata={
                    "type": "package_purchase",
                    "tenant_id": str(user.tenant.id),
                    "client_id": str(user.id),
                    "package_type_id": str(package_type.id)
                },
                success_url=success_url,
                cancel_url=cancel_url,
            )
            return Response({"url": session.url}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error creating checkout session for package {package_type_id}: {str(e)}")
            return Response({"error": "Failed to create checkout session."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class PackageCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, package_id):
        user = request.user
        if user.role != UserRole.CLIENT:
            return Response({"error": "Only clients can cancel packages."}, status=status.HTTP_403_FORBIDDEN)

        from apps.scheduling.models import Package
        try:
            package = Package.objects.get(id=package_id, client=user, tenant=user.tenant)
        except Package.DoesNotExist:
            return Response({"error": "Package not found."}, status=status.HTTP_404_NOT_FOUND)

        if not package.stripe_subscription_id:
            return Response({"error": "Package does not have an active subscription."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            stripe.Subscription.modify(
                package.stripe_subscription_id,
                cancel_at_period_end=True
            )
            package.cancel_at_period_end = True
            package.save(update_fields=['cancel_at_period_end'])
            return Response({"detail": "Subscription will be canceled at the end of the billing period."}, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error canceling subscription for package {package_id}: {str(e)}")
            return Response({"error": "Failed to cancel subscription."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TenantFinanceSummaryAPIView(APIView):
    """
    Returns a financial summary for the Gym Owner's dashboard.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role != UserRole.GYM_OWNER:
            return Response({"error": "Only Gym Owners can view financial summaries."}, status=status.HTTP_403_FORBIDDEN)

        tenant = user.tenant
        if not tenant:
            return Response({"error": "No tenant associated with your account."}, status=status.HTTP_400_BAD_REQUEST)

        from django.db.models import Sum, Q
        import decimal

        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        # Base filters
        charges_filter = Q(type=PlatformLedger.TransactionType.CHARGE)
        payouts_filter = Q()
        subs_filter = Q(type=PlatformLedger.TransactionType.SUBSCRIPTION)

        if start_date:
            charges_filter &= Q(created_at__gte=start_date)
            payouts_filter &= Q(created_at__gte=start_date)
            subs_filter &= Q(created_at__gte=start_date)
        if end_date:
            charges_filter &= Q(created_at__lte=end_date)
            payouts_filter &= Q(created_at__lte=end_date)
            subs_filter &= Q(created_at__lte=end_date)

        # 1. Package Sales (type='charge')
        charges = PlatformLedger.objects.filter(charges_filter)
        total_sales_gross = charges.aggregate(val=Sum('amount_gross'))['val'] or decimal.Decimal('0.00')
        total_platform_fees = charges.aggregate(val=Sum('platform_fee'))['val'] or decimal.Decimal('0.00')
        total_net_earned = charges.aggregate(val=Sum('amount_net'))['val'] or decimal.Decimal('0.00')

        # 2. Payouts (TenantPayout)
        payouts = TenantPayout.objects.filter(payouts_filter)
        total_paid_payouts = payouts.filter(status=TenantPayout.StatusChoices.PAID).aggregate(val=Sum('amount'))['val'] or decimal.Decimal('0.00')
        total_pending_payouts = charges.filter(status=PlatformLedger.StatusChoices.PENDING).aggregate(val=Sum('amount_net'))['val'] or decimal.Decimal('0.00')

        # 3. Platform expenses (type='sub')
        subs = PlatformLedger.objects.filter(subs_filter)
        total_expenses = subs.aggregate(val=Sum('amount_gross'))['val'] or decimal.Decimal('0.00')

        return Response({
            "package_revenue": {
                "total_gross": str(total_sales_gross),
                "total_platform_fees": str(total_platform_fees),
                "total_net_earned": str(total_net_earned),
            },
            "payouts": {
                "total_paid": str(total_paid_payouts),
                "total_pending": str(total_pending_payouts),
            },
            "platform_expenses": {
                "total_paid": str(total_expenses),
            },
            "stripe_connect": {
                "is_connected": bool(tenant.stripe_account_id),
                "stripe_account_id": tenant.stripe_account_id,
            }
        }, status=status.HTTP_200_OK)


class TenantPayoutListView(generics.ListAPIView):
    """
    Lists payout history for the current Gym Owner (tenant-scoped).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = TenantPayoutSerializer

    def get_queryset(self):
        user = self.request.user
        if user.role != UserRole.GYM_OWNER:
            return TenantPayout.objects.none()
        
        queryset = TenantPayout.objects.all()
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date:
            queryset = queryset.filter(created_at__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__lte=end_date)
        return queryset.order_by('-created_at')


class PlatformTenantFinanceBreakdownAPIView(APIView):
    """
    Returns a gym-by-gym financial breakdown list for Platform Admins.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role != UserRole.PLATFORM_ADMIN:
            return Response({"error": "Only Platform Admins can view gym breakdowns."}, status=status.HTTP_403_FORBIDDEN)

        from django.db.models import Sum, Q
        from apps.core.tenants.context import bypass_tenant_isolation
        import decimal

        search = request.query_params.get('search')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        # Standard page & page_size pagination
        try:
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get('page_size', 10))
        except ValueError:
            page = 1
            page_size = 10

        breakdown = []
        with bypass_tenant_isolation():
            tenants = Tenant.objects.all().order_by('name')
            if search:
                tenants = tenants.filter(Q(name__icontains=search) | Q(subdomain__icontains=search))
            
            total_count = tenants.count()
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            paginated_tenants = tenants[start_idx:end_idx]

            for tenant in paginated_tenants:
                charges_filter = Q(tenant=tenant, type=PlatformLedger.TransactionType.CHARGE)
                payouts_filter = Q(tenant=tenant)

                if start_date:
                    charges_filter &= Q(created_at__gte=start_date)
                    payouts_filter &= Q(created_at__gte=start_date)
                if end_date:
                    charges_filter &= Q(created_at__lte=end_date)
                    payouts_filter &= Q(created_at__lte=end_date)

                charges = PlatformLedger.all_objects.filter(charges_filter)
                total_sales_gross = charges.aggregate(val=Sum('amount_gross'))['val'] or decimal.Decimal('0.00')
                total_platform_fees = charges.aggregate(val=Sum('platform_fee'))['val'] or decimal.Decimal('0.00')

                payouts = TenantPayout.all_objects.filter(payouts_filter)
                total_paid_payouts = payouts.filter(status=TenantPayout.StatusChoices.PAID).aggregate(val=Sum('amount'))['val'] or decimal.Decimal('0.00')
                total_pending_payouts = charges.filter(status=PlatformLedger.StatusChoices.PENDING).aggregate(val=Sum('amount_net'))['val'] or decimal.Decimal('0.00')

                breakdown.append({
                    "tenant_id": str(tenant.id),
                    "tenant_name": tenant.name,
                    "subdomain": tenant.subdomain,
                    "total_client_sales_gross": str(total_sales_gross),
                    "total_platform_fees": str(total_platform_fees),
                    "total_paid_payouts": str(total_paid_payouts),
                    "total_pending_payouts": str(total_pending_payouts),
                    "stripe_connect_status": "Connected" if tenant.stripe_account_id else "Not Connected"
                })

        return Response({
            "count": total_count,
            "page": page,
            "page_size": page_size,
            "results": breakdown
        }, status=status.HTTP_200_OK)

