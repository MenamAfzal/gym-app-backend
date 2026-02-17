import stripe
from django.conf import settings
from django.urls import reverse
from apps.core.tenants.models import Tenant, Plan

stripe.api_key = settings.STRIPE_SECRET_KEY

class StripeService:
    """
    Wrapper for Stripe API interactions to keep business logic clean.
    """

    @staticmethod
    def create_customer(tenant: Tenant, email: str):
        """
        Creates a Stripe Customer for the Tenant.
        """
        if tenant.stripe_customer_id:
            return tenant.stripe_customer_id
        
        try:
            customer = stripe.Customer.create(
                email=email,
                name=tenant.name,
                metadata={
                    "tenant_id": str(tenant.id),
                    "subdomain": tenant.subdomain
                }
            )
            tenant.stripe_customer_id = customer.id
            tenant.save()
            return customer.id
        except stripe.error.StripeError as e:
            # Log error here
            raise e

    @staticmethod
    def create_checkout_session(tenant: Tenant, plan: Plan, user_email: str, success_url: str, cancel_url: str):
        """
        Generates a Stripe Checkout Session URL for subscription.
        """
        if not tenant.stripe_customer_id:
            StripeService.create_customer(tenant, user_email)

        if not plan.stripe_price_id:
            raise ValueError("This plan is not linked to a Stripe Price ID.")

        session = stripe.checkout.Session.create(
            customer=tenant.stripe_customer_id,
            payment_method_types=['card'],
            line_items=[{
                'price': plan.stripe_price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=success_url,
            cancel_url=cancel_url,
            # Metadata is crucial for the Webhook later
            metadata={
                "tenant_id": str(tenant.id),
                "plan_id": str(plan.id)
            },
            subscription_data={
                "metadata": {
                     "tenant_id": str(tenant.id),
                     "plan_id": str(plan.id)
                }
            }
        )
        return session.url

    @staticmethod
    def create_portal_session(tenant: Tenant, return_url: str):
        """
        Generates a URL for the Self-Serve Customer Portal (Update Card, Cancel).
        """
        if not tenant.stripe_customer_id:
            raise ValueError("Tenant has no Stripe Customer ID.")

        session = stripe.billing_portal.Session.create(
            customer=tenant.stripe_customer_id,
            return_url=return_url
        )
        return session.url
    