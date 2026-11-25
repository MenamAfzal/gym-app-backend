from datetime import timedelta
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone

from apps.core.tenants.context import set_current_tenant, reset_current_tenant
from apps.core.tenants.models import Tenant
from apps.payments.models import PlatformLedger
from apps.payments.stripe_package_service import StripePackageService
from apps.scheduling.models import Location, Package, PackageType, Payment
from apps.users.models import User, UserRole


class PackageSubscriptionRenewalTestCase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Renewal Studio", subdomain="renewal-studio", currency="gbp")
        self.token = set_current_tenant(self.tenant)
        self.addCleanup(lambda: reset_current_tenant(self.token))

        self.location = Location.objects.create(
            tenant=self.tenant,
            name="Main Club",
            address="1 High St",
            timezone="UTC",
        )
        self.pkg_type = PackageType.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Monthly 12-Pack",
            credit_count=12,
            validity_days=30,
            price=Decimal("120.00"),
            billing_cycle="monthly",
        )
        self.client_user = User.objects.create_user(
            email="member@renewalstudio.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            first_name="Liam",
            last_name="Payne",
        )
        self.now = timezone.now()
        self.package = Package.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            package_type=self.pkg_type,
            credits_remaining=1,
            total_credits_allocated=12,
            expires_at=self.now + timedelta(days=2),
            stripe_subscription_id="sub_test_renewal_123",
            status="active",
            price=Decimal("120.00"),
        )

    def test_renewal_via_stripe_2025_basil_invoice_schema_and_ledger(self):
        """
        Verifies that an invoice.payment_succeeded webhook using the 2025+ Stripe API schema
        (where subscription is nested under parent.subscription_details.subscription)
        refills credits, extends expires_at, creates a Payment, and records a PlatformLedger charge.
        """
        future_period_end = int((self.now + timedelta(days=32)).timestamp())
        basil_invoice = {
            "id": "in_basil_renewal_001",
            "billing_reason": "subscription_cycle",
            "amount_paid": 12000,
            "currency": "gbp",
            "parent": {
                "type": "subscription_details",
                "subscription_details": {
                    "subscription": "sub_test_renewal_123",
                },
            },
            "lines": {
                "data": [
                    {
                        "period": {
                            "end": future_period_end,
                        }
                    }
                ]
            },
        }

        StripePackageService.handle_invoice_payment_succeeded(basil_invoice)

        self.package.refresh_from_db()
        self.assertEqual(self.package.credits_remaining, 12)
        self.assertEqual(self.package.status, "active")
        self.assertEqual(int(self.package.expires_at.timestamp()), future_period_end)

        # Verify Payment record created
        payment = Payment.all_objects.filter(idempotency_key="in_basil_renewal_001").first()
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, Decimal("120.00"))

        # Verify PlatformLedger charge entry created
        ledger = PlatformLedger.all_objects.filter(transaction_id="in_basil_renewal_001").first()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.amount_gross, Decimal("120.00"))

        # Idempotency check: duplicate webhook does not create second payment
        StripePackageService.handle_invoice_payment_succeeded(basil_invoice)
        self.assertEqual(Payment.all_objects.filter(idempotency_key="in_basil_renewal_001").count(), 1)

    def test_subscription_updated_safeguard_with_nested_items_current_period_end(self):
        """
        Verifies that customer.subscription.updated with 2025+ items.data[0].current_period_end
        extends package expiration and refills credits.
        """
        future_period_end = int((self.now + timedelta(days=35)).timestamp())
        sub_payload = {
            "id": "sub_test_renewal_123",
            "status": "active",
            "cancel_at_period_end": False,
            "items": {
                "data": [
                    {
                        "current_period_end": future_period_end,
                    }
                ]
            },
        }

        StripePackageService.handle_subscription_updated(sub_payload)

        self.package.refresh_from_db()
        self.assertEqual(self.package.credits_remaining, 12)
        self.assertEqual(int(self.package.expires_at.timestamp()), future_period_end)

    def test_custom_recurring_billing_period_serializer_and_stripe_sync(self):
        """
        Verifies creating a PackageType with billing_cycle='custom', custom_billing_interval='week'/'days'/'months'/'year',
        and custom_billing_interval_count (e.g. Every 4 weeks, Every 45 days), and verifies Stripe Price.create receives
        recurring={'interval': 'week', 'interval_count': 4}.
        """
        from unittest.mock import patch, MagicMock
        from apps.scheduling.serializers import PackageTypeSerializer

        mock_product = MagicMock()
        mock_product.id = "prod_custom_4w"
        mock_price = MagicMock()
        mock_price.id = "price_custom_4w"

        with patch("apps.payments.stripe_package_service.stripe.Product.create", return_value=mock_product) as prod_create, \
             patch("apps.payments.stripe_package_service.stripe.Price.create", return_value=mock_price) as price_create:

            serializer = PackageTypeSerializer(data={
                "location": str(self.location.id),
                "name": "4-Week Transformation Pack",
                "credit_count": 16,
                "price": "180.00",
                "validity_days": 28,
                "billing_cycle": "custom",
                "custom_billing_interval": "weeks",  # tests plural alias normalization -> 'week'
                "custom_billing_interval_count": 4,
            })
            self.assertTrue(serializer.is_valid(), serializer.errors)
            pkg_type = serializer.save(tenant=self.tenant)

            self.assertEqual(pkg_type.billing_cycle, "custom")
            self.assertEqual(pkg_type.custom_billing_interval, "week")
            self.assertEqual(pkg_type.custom_billing_interval_count, 4)
            self.assertEqual(pkg_type.billing_period_display, "Every 4 weeks")
            self.assertEqual(pkg_type.get_stripe_recurring_config(), ("week", 4))

            price_create.assert_called_once()
            _, kwargs = price_create.call_args
            self.assertEqual(kwargs["recurring"], {"interval": "week", "interval_count": 4})


