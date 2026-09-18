from datetime import timedelta
from django.test import TestCase, RequestFactory
from django.utils import timezone
from apps.core.tenants.models import Tenant
from apps.core.tenants.services import TenantEntitlementService
from apps.core.permissions import TenantFeaturePermission
from apps.payments.models import (
    BillingFeature,
    BillingPlan,
    TenantBillingSubscription,
    GymFeatureEntitlement,
)
from apps.payments.permissions import GymFeaturePermission
from apps.payments.billing_service import FeatureBillingService, BillingValidationError
from apps.payments.billing_serializers import (
    BillingFeatureSerializer,
    GymFeatureEntitlementSerializer,
    TenantBillingSubscriptionSerializer,
)
from apps.users.models import User, UserRole


class GymFeatureEntitlementDecouplingTests(TestCase):
    """
    Tests ensuring platform-level feature status (is_active) is decoupled
    from gym-level feature entitlements (GymFeatureEntitlement).
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.tenant = Tenant.objects.create(
            name="Alpha Fitness",
            subdomain="alphafitness",
            is_active=True,
        )
        self.user = User.objects.create_user(
            email="owner@alphafitness.com",
            password="password123",
            tenant=self.tenant,
            role=UserRole.GYM_OWNER,
        )

        self.plan, _ = BillingPlan.objects.get_or_create(
            slug=BillingPlan.PlanSlug.BASIC,
            defaults={
                "name": "Basic",
                "allowed_feature_count": 3,
                "is_public": True,
            }
        )

        self.feature = BillingFeature.objects.create(
            name="Reflection Logger",
            code="reflection_logger",
            description="Premium reflection logger",
            price=29.00,
            billing_cycle="monthly",
            stripe_price_id="price_test_123",
            is_active=True,
        )

        future_end = timezone.now() + timedelta(days=30)
        self.subscription = TenantBillingSubscription.objects.create(
            tenant=self.tenant,
            billing_plan=self.plan,
            status=TenantBillingSubscription.StatusChoices.ACTIVE,
            current_period_end=future_end,
            stripe_subscription_id="sub_test_123",
        )
        self.subscription.active_features.add(self.feature)
        GymFeatureEntitlement.sync_for_subscription(self.subscription)

    def test_active_feature_grants_access_initially(self):
        """When feature is active globally, subscribed gym has access."""
        self.assertTrue(GymFeatureEntitlement.is_gym_entitled(self.tenant, "reflection_logger"))
        self.assertTrue(TenantEntitlementService.has_feature(self.tenant, "reflection_logger"))

    def test_global_deactivation_does_not_revoke_active_gym_entitlement(self):
        """
        CRITICAL: Deactivating a global feature (is_active=False) MUST NOT revoke access
        for existing gyms whose subscription/billing period is still active.
        """
        # Platform Admin deactivates feature globally
        self.feature.is_active = False
        self.feature.save()

        # Refresh from DB
        self.feature.refresh_from_db()
        self.assertFalse(self.feature.is_active)

        # Invalidate cache if any
        TenantEntitlementService.invalidate_tenant_cache(self.tenant)

        # 1. GymFeatureEntitlement model check
        self.assertTrue(
            GymFeatureEntitlement.is_gym_entitled(self.tenant, "reflection_logger"),
            "Gym should retain entitlement even though global feature is_active=False"
        )
        self.assertTrue(
            GymFeatureEntitlement.is_gym_entitled(self.tenant, self.feature),
            "Gym should retain entitlement when passing BillingFeature object"
        )

        # 2. TenantEntitlementService check
        self.assertTrue(
            TenantEntitlementService.has_feature(self.tenant, "reflection_logger"),
            "TenantEntitlementService.has_feature must return True for active gym entitlement"
        )

        # 3. GymFeaturePermission DRF permission check
        request = self.factory.get("/")
        request.tenant = self.tenant
        request.user = self.user

        class TestGymPermission(GymFeaturePermission):
            feature_code = "reflection_logger"

        perm = TestGymPermission()
        self.assertTrue(
            perm.has_permission(request, None),
            "GymFeaturePermission must grant access to active gym despite global deactivation"
        )

        # 4. TenantFeaturePermission DRF permission check
        class TestTenantFeaturePermission(TenantFeaturePermission):
            feature_key = "reflection_logger"

        tenant_perm = TestTenantFeaturePermission()
        self.assertTrue(
            tenant_perm.has_permission(request, None),
            "TenantFeaturePermission must grant access to active gym despite global deactivation"
        )

    def test_global_deactivation_prevents_new_purchases(self):
        """Deactivating feature globally prevents other gyms from purchasing it."""
        self.feature.is_active = False
        self.feature.save()

        new_tenant = Tenant.objects.create(
            name="Beta Fitness",
            subdomain="betafitness",
            is_active=True,
        )

        # Validating feature selection for new checkout must fail because feature is inactive
        with self.assertRaises(BillingValidationError) as ctx:
            FeatureBillingService.create_checkout_session(
                tenant=new_tenant,
                plan_slug=self.plan.slug,
                feature_ids=[self.feature.id],
            )
        self.assertIn("active", str(ctx.exception).lower())

    def test_expired_billing_cycle_revokes_entitlement(self):
        """When the billing period ends in the past, entitlement is revoked."""
        past_end = timezone.now() - timedelta(days=1)
        self.subscription.current_period_end = past_end
        self.subscription.save()

        entitlement = GymFeatureEntitlement.all_objects.get(tenant=self.tenant, feature=self.feature)
        entitlement.current_period_end = past_end
        entitlement.save()

        TenantEntitlementService.invalidate_tenant_cache(self.tenant)

        self.assertFalse(
            GymFeatureEntitlement.is_gym_entitled(self.tenant, "reflection_logger"),
            "Expired billing cycle must revoke entitlement"
        )
        self.assertFalse(
            TenantEntitlementService.has_feature(self.tenant, "reflection_logger"),
            "Expired billing cycle must cause TenantEntitlementService.has_feature to return False"
        )

    def test_canceled_subscription_revokes_entitlement(self):
        """When subscription status is canceled, entitlement is revoked."""
        self.subscription.status = TenantBillingSubscription.StatusChoices.CANCELED
        self.subscription.save()

        entitlement = GymFeatureEntitlement.all_objects.get(tenant=self.tenant, feature=self.feature)
        entitlement.is_active = False
        entitlement.save()

        TenantEntitlementService.invalidate_tenant_cache(self.tenant)

        self.assertFalse(
            GymFeatureEntitlement.is_gym_entitled(self.tenant, "reflection_logger"),
            "Canceled subscription must revoke entitlement"
        )

    def test_serializers_decoupling(self):
        """Verify serializers clearly decouple gym entitlement from platform active status."""
        self.feature.is_active = False
        self.feature.save()

        # BillingFeatureSerializer
        feat_data = BillingFeatureSerializer(self.feature).data
        self.assertFalse(feat_data["is_active"])
        self.assertFalse(feat_data["platform_is_active"])

        # GymFeatureEntitlementSerializer
        entitlement = GymFeatureEntitlement.all_objects.get(tenant=self.tenant, feature=self.feature)
        ent_data = GymFeatureEntitlementSerializer(entitlement).data
        self.assertTrue(ent_data["is_active"])
        self.assertTrue(ent_data["is_entitled"])

        # TenantBillingSubscriptionSerializer
        sub_data = TenantBillingSubscriptionSerializer(self.subscription).data
        self.assertEqual(len(sub_data["feature_entitlements"]), 1)
        self.assertTrue(sub_data["feature_entitlements"][0]["is_entitled"])
