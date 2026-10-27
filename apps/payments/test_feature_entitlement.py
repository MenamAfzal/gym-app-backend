import datetime
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from django.contrib.auth import get_user_model
from apps.core.tenants.context import set_current_tenant, reset_current_tenant
from apps.core.tenants.models import Tenant
from apps.core.tenants.services import TenantEntitlementService
from apps.payments.models import BillingFeature, BillingPlan, TenantBillingSubscription
from apps.users.models import UserRole

User = get_user_model()


class FeatureEntitlementDecouplingTests(APITestCase):
    def setUp(self):
        # Create gym tenant
        self.tenant = Tenant.objects.create(
            name="Alpha Fitness",
            subdomain="alpha-fit",
        )
        self.tenant_token = set_current_tenant(self.tenant)

        # Create gym owner user
        self.gym_owner = User.objects.create_user(
            email="owner@alphafit.com",
            password="Password123!",
            first_name="Alpha",
            last_name="Owner",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant,
        )

        # Create platform admin user
        self.platform_admin = User.objects.create_user(
            email="admin@platform.com",
            password="Password123!",
            first_name="Platform",
            last_name="Admin",
            role=UserRole.PLATFORM_ADMIN,
            is_staff=True,
            tenant=None,
        )

        # Create or retrieve BillingPlan
        self.plan, _ = BillingPlan.objects.get_or_create(
            slug=BillingPlan.PlanSlug.CUSTOM,
            defaults={
                "name": "Custom Plan",
                "is_public": True,
                "allowed_feature_count": -1,
            }
        )

        # Create or retrieve BillingFeature
        self.feature, _ = BillingFeature.objects.get_or_create(
            code="food_logger",
            defaults={
                "name": "Food Logger",
                "description": "Track nutrition and meals",
                "price": Decimal("15.00"),
                "billing_cycle": "monthly",
                "is_active": True,
            }
        )
        self.feature.is_active = True
        self.feature.save()

        # Clean any default provisioning subscription for clean test state
        TenantBillingSubscription.all_objects.filter(tenant=self.tenant).delete()

        # Create active TenantBillingSubscription for the gym with future period end
        self.future_date = timezone.now() + datetime.timedelta(days=30)
        self.subscription = TenantBillingSubscription.all_objects.create(
            tenant=self.tenant,
            billing_plan=self.plan,
            status=TenantBillingSubscription.StatusChoices.ACTIVE,
            current_period_end=self.future_date,
            stripe_subscription_id="sub_test_12345",
        )
        self.subscription.active_features.add(self.feature)

    def tearDown(self):
        reset_current_tenant(self.tenant_token)

    @patch("stripe.Subscription.retrieve")
    def test_active_gym_subscription_returns_entitled_feature(self, mock_stripe_sub):
        """
        Verify that when a gym has an active subscription, its features return is_active=True,
        is_entitled=True, and platform_is_active=True.
        """
        mock_sub = MagicMock()
        mock_sub.cancel_at_period_end = False
        mock_sub.current_period_end = int(self.future_date.timestamp())
        mock_stripe_sub.return_value = mock_sub

        self.client.force_authenticate(user=self.gym_owner)
        res = self.client.get("/api/v1/payments/billing/subscription/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

        sub_data = res.data[0]
        self.assertTrue(sub_data["is_entitled"])
        self.assertEqual(len(sub_data["active_features"]), 1)

        feat_data = sub_data["active_features"][0]
        self.assertEqual(feat_data["code"], "food_logger")
        self.assertTrue(feat_data["is_active"])
        self.assertTrue(feat_data["is_entitled"])
        self.assertTrue(feat_data["platform_is_active"])
        self.assertTrue(feat_data["current_period_end"].startswith(
            self.future_date.strftime("%Y-%m-%d")
        ))

        # Also verify backend TenantEntitlementService
        has_access = TenantEntitlementService.has_feature(self.tenant, "food_logger")
        self.assertTrue(has_access)

    @patch("stripe.Subscription.retrieve")
    @patch("apps.payments.billing_service.FeatureBillingService.sync_feature_to_stripe")
    def test_platform_admin_deactivation_does_not_revoke_existing_gym_entitlement(
        self, mock_sync_stripe, mock_stripe_sub
    ):
        """
        When Platform Admin deactivates a feature (is_active=False), existing gyms with an
        active subscription must CONTINUE to have access (is_active=True, is_entitled=True).
        Only platform_is_active becomes False.
        """
        mock_sub = MagicMock()
        mock_sub.cancel_at_period_end = False
        mock_sub.current_period_end = int(self.future_date.timestamp())
        mock_stripe_sub.return_value = mock_sub

        # Platform Admin deactivates feature
        self.client.force_authenticate(user=self.platform_admin)
        res_patch = self.client.patch(
            f"/api/v1/payments/billing/features/{self.feature.id}/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(res_patch.status_code, status.HTTP_200_OK)
        self.feature.refresh_from_db()
        self.assertFalse(self.feature.is_active)

        # Catalog check: Non-admin gym does not see deactivated feature for new purchases
        self.client.force_authenticate(user=self.gym_owner)
        res_catalog = self.client.get("/api/v1/payments/billing/features/")
        self.assertEqual(res_catalog.status_code, status.HTTP_200_OK)
        catalog_codes = [f["code"] for f in res_catalog.data]
        self.assertNotIn("food_logger", catalog_codes)

        # Subscription check: Existing gym STILL has active entitlement!
        res_sub = self.client.get("/api/v1/payments/billing/subscription/")
        self.assertEqual(res_sub.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_sub.data), 1)

        feat_data = res_sub.data[0]["active_features"][0]
        self.assertEqual(feat_data["code"], "food_logger")
        # Critical assertion: is_active is True for existing gym entitlement!
        self.assertTrue(feat_data["is_active"])
        self.assertTrue(feat_data["is_entitled"])
        # platform_is_active is False
        self.assertFalse(feat_data["platform_is_active"])

        # Backend entitlement service check: access is still granted
        TenantEntitlementService.invalidate_tenant_cache(self.tenant)
        has_access = TenantEntitlementService.has_feature(self.tenant, "food_logger")
        self.assertTrue(has_access)

    @patch("stripe.Subscription.retrieve")
    @patch("apps.payments.billing_service.FeatureBillingService.delete_feature_from_stripe")
    def test_platform_admin_soft_delete_preserves_existing_gym_access(
        self, mock_del_stripe, mock_stripe_sub
    ):
        """
        When Platform Admin deletes a feature that has subscriptions, the feature is soft-deleted
        (feature.is_active=False) and existing gym subscribers preserve access.
        """
        mock_sub = MagicMock()
        mock_sub.cancel_at_period_end = False
        mock_sub.current_period_end = int(self.future_date.timestamp())
        mock_stripe_sub.return_value = mock_sub

        self.client.force_authenticate(user=self.platform_admin)
        res_del = self.client.delete(f"/api/v1/payments/billing/features/{self.feature.id}/")
        self.assertEqual(res_del.status_code, status.HTTP_204_NO_CONTENT)

        self.feature.refresh_from_db()
        self.assertFalse(self.feature.is_active)

        # Existing subscriber gym still has access
        self.client.force_authenticate(user=self.gym_owner)
        res_sub = self.client.get("/api/v1/payments/billing/subscription/")
        self.assertEqual(res_sub.status_code, status.HTTP_200_OK)
        feat_data = res_sub.data[0]["active_features"][0]
        self.assertTrue(feat_data["is_active"])
        self.assertTrue(feat_data["is_entitled"])
        self.assertFalse(feat_data["platform_is_active"])

    def test_gym_entitlement_disabled_when_subscription_canceled(self):
        """
        When a gym's subscription is canceled, its feature entitlement becomes inactive (is_active=False).
        """
        self.subscription.status = TenantBillingSubscription.StatusChoices.CANCELED
        self.subscription.save()

        self.client.force_authenticate(user=self.gym_owner)
        res_sub = self.client.get("/api/v1/payments/billing/subscription/")
        # CANCELED subscriptions are excluded from active list in TenantBillingSubscriptionView
        self.assertEqual(res_sub.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res_sub.data), 0)

        # Directly testing serializer output for canceled subscription
        from apps.payments.billing_serializers import TenantBillingSubscriptionSerializer
        data = TenantBillingSubscriptionSerializer(self.subscription).data
        self.assertFalse(data["is_entitled"])
        self.assertFalse(data["active_features"][0]["is_active"])
        self.assertFalse(data["active_features"][0]["is_entitled"])

        # Backend service check
        TenantEntitlementService.invalidate_tenant_cache(self.tenant)
        has_access = TenantEntitlementService.has_feature(self.tenant, "food_logger")
        self.assertFalse(has_access)

    def test_gym_entitlement_disabled_when_period_expires(self):
        """
        When a gym's current_period_end has passed without renewal, is_entitled becomes False.
        """
        # Set current_period_end in the past
        self.subscription.current_period_end = timezone.now() - datetime.timedelta(days=1)
        self.subscription.save()

        from apps.payments.billing_serializers import TenantBillingSubscriptionSerializer
        data = TenantBillingSubscriptionSerializer(self.subscription).data
        self.assertFalse(data["is_entitled"])
        self.assertFalse(data["active_features"][0]["is_active"])
        self.assertFalse(data["active_features"][0]["is_entitled"])

        # Backend service check
        TenantEntitlementService.invalidate_tenant_cache(self.tenant)
        has_access = TenantEntitlementService.has_feature(self.tenant, "food_logger")
        self.assertFalse(has_access)
