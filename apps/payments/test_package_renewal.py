"""
Tests for Package Subscription Renewal via Stripe Webhooks
"""
import json
import uuid
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant, reset_current_tenant
from apps.users.models import User, UserRole
from apps.scheduling.models import Location, PackageType, Package, Payment
from apps.payments.stripe_package_service import StripePackageService


class PackageRenewalTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # 1. Create Tenant
        self.tenant = Tenant.objects.create(
            name="Apex Fitness Club",
            subdomain="apex-test"
        )
        self.tenant_token = set_current_tenant(self.tenant)

        # 2. Create Location
        self.location = Location.objects.create(
            tenant=self.tenant,
            name="Apex Main Gym",
            address="100 Fitness Blvd",
            timezone="UTC"
        )

        # 3. Create Client User
        self.user = User.objects.create_user(
            email="member.jane@example.com",
            password="MemberPassword123!",
            first_name="Jane",
            last_name="Doe",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )

        # 4. Create Package Type (e.g. 10 credits / month, $60)
        self.package_type = PackageType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Monthly 10-Class Pack",
            credit_count=10,
            price=Decimal("60.00"),
            validity_days=30,
            billing_cycle="monthly"
        )

        # 5. Create Client Package that has consumed credits and is near expiration
        self.now = timezone.now()
        self.sub_id = f"sub_pkg_renew_{uuid.uuid4().hex[:8]}"
        self.package = Package.objects.create(
            tenant=self.tenant,
            client=self.user,
            package_type=self.package_type,
            credits_remaining=0,
            total_credits_allocated=10,
            expires_at=self.now + timedelta(days=1),
            stripe_subscription_id=self.sub_id,
            status="active"
        )

    def tearDown(self):
        reset_current_tenant(self.tenant_token)

    def test_handle_invoice_payment_succeeded_renews_credits_and_extends_expiry(self):
        """
        When invoice.payment_succeeded with billing_reason='subscription_cycle' arrives,
        credits must be refilled to credit_count, expires_at extended, and a Payment recorded.
        """
        invoice_id = f"in_test_{uuid.uuid4().hex[:8]}"
        period_end = int((self.now + timedelta(days=30)).timestamp())

        invoice_payload = {
            "id": invoice_id,
            "subscription": self.sub_id,
            "billing_reason": "subscription_cycle",
            "amount_paid": 6000,
            "lines": {
                "data": [
                    {
                        "period": {
                            "end": period_end
                        }
                    }
                ]
            }
        }

        StripePackageService.handle_invoice_payment_succeeded(invoice_payload)

        self.package.refresh_from_db()
        self.assertEqual(self.package.credits_remaining, 10)
        self.assertEqual(self.package.total_credits_allocated, 10)
        self.assertEqual(self.package.status, "active")
        self.assertGreater(self.package.expires_at, self.now + timedelta(days=25))

        # Check renewal Payment record
        payment = Payment.all_objects.get(idempotency_key=invoice_id)
        self.assertEqual(payment.tenant, self.tenant)
        self.assertEqual(payment.client, self.user)
        self.assertEqual(payment.package, self.package)
        self.assertEqual(payment.package_type, self.package_type)
        self.assertEqual(payment.amount, Decimal("60.00"))
        self.assertEqual(payment.status, "completed")
        self.assertEqual(payment.type, "package_purchase")

    def test_invoice_payment_succeeded_idempotency(self):
        """
        Processing the same invoice twice should not duplicate Payment records or double credits.
        """
        invoice_id = f"in_idemp_{uuid.uuid4().hex[:8]}"
        invoice_payload = {
            "id": invoice_id,
            "subscription": self.sub_id,
            "billing_reason": "subscription_cycle",
            "amount_paid": 6000,
        }

        # Process first time
        StripePackageService.handle_invoice_payment_succeeded(invoice_payload)
        initial_payments_count = Payment.all_objects.filter(idempotency_key=invoice_id).count()
        self.assertEqual(initial_payments_count, 1)

        # Consume 3 credits
        self.package.credits_remaining = 7
        self.package.save(update_fields=["credits_remaining"])

        # Process second time (same invoice)
        StripePackageService.handle_invoice_payment_succeeded(invoice_payload)
        second_payments_count = Payment.all_objects.filter(idempotency_key=invoice_id).count()
        self.assertEqual(second_payments_count, 1)

        # Credits should NOT be reset again on duplicate invoice delivery
        self.package.refresh_from_db()
        self.assertEqual(self.package.credits_remaining, 7)

    def test_initial_subscription_create_does_not_duplicate_credits(self):
        """
        An invoice with billing_reason='subscription_create' is the initial checkout invoice
        and should NOT trigger a second renewal grant.
        """
        invoice_payload = {
            "id": f"in_create_{uuid.uuid4().hex[:8]}",
            "subscription": self.sub_id,
            "billing_reason": "subscription_create",
            "amount_paid": 6000,
        }

        StripePackageService.handle_invoice_payment_succeeded(invoice_payload)

        self.package.refresh_from_db()
        # Credits remain 0 (untouched because initial grant is handled at checkout)
        self.assertEqual(self.package.credits_remaining, 0)
        self.assertFalse(Payment.all_objects.filter(idempotency_key=invoice_payload["id"]).exists())

    def test_subscription_updated_safeguard_refills_credits(self):
        """
        If Stripe sends customer.subscription.updated where current_period_end advances,
        it should refill credits as a safeguard.
        """
        new_period_end = int((self.now + timedelta(days=30)).timestamp())
        sub_payload = {
            "id": self.sub_id,
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": new_period_end
        }

        StripePackageService.handle_subscription_updated(sub_payload)

        self.package.refresh_from_db()
        self.assertEqual(self.package.credits_remaining, 10)
        self.assertEqual(self.package.status, "active")
        self.assertGreater(self.package.expires_at, self.now + timedelta(days=25))

    @patch("stripe.Webhook.construct_event")
    def test_stripe_checkout_webhook_renews_package(self, mock_construct_event):
        """
        Sending invoice.payment_succeeded to the checkout webhook endpoint should renew the package.
        """
        invoice_id = f"in_webhook_checkout_{uuid.uuid4().hex[:8]}"
        payload = {
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "id": invoice_id,
                    "subscription": self.sub_id,
                    "billing_reason": "subscription_cycle",
                    "amount_paid": 6000,
                }
            }
        }
        mock_construct_event.return_value = payload

        response = self.client.post(
            reverse("stripe-checkout-webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=mock_sig"
        )
        self.assertEqual(response.status_code, 200)

        self.package.refresh_from_db()
        self.assertEqual(self.package.credits_remaining, 10)
        self.assertTrue(Payment.all_objects.filter(idempotency_key=invoice_id).exists())

    @patch("stripe.Webhook.construct_event")
    def test_stripe_webhook_renews_package(self, mock_construct_event):
        """
        Sending invoice.payment_succeeded to the standard payments webhook endpoint should renew the package.
        """
        invoice_id = f"in_webhook_std_{uuid.uuid4().hex[:8]}"
        payload = {
            "type": "invoice.payment_succeeded",
            "data": {
                "object": {
                    "id": invoice_id,
                    "subscription": self.sub_id,
                    "billing_reason": "subscription_cycle",
                    "amount_paid": 6000,
                }
            }
        }
        mock_construct_event.return_value = payload

        response = self.client.post(
            reverse("stripe-webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=mock_sig"
        )
        self.assertEqual(response.status_code, 200)

        self.package.refresh_from_db()
        self.assertEqual(self.package.credits_remaining, 10)
        self.assertTrue(Payment.all_objects.filter(idempotency_key=invoice_id).exists())
