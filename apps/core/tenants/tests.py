"""
Tenant Model Tests

Basic test scaffolding for tenant models.
"""
from django.test import TestCase
from rest_framework.test import APITestCase
from apps.core.tenants.models import Tenant, Plan, Feature


class TenantModelTest(TestCase):
    """Test cases for Tenant model."""
    
    def setUp(self):
        """Set up test data."""
        self.tenant = Tenant.objects.create(
            name="Test Gym",
            subdomain="testgym",
            is_active=True
        )
    
    def test_tenant_creation(self):
        """Test tenant can be created."""
        self.assertEqual(self.tenant.name, "Test Gym")
        self.assertEqual(self.tenant.subdomain, "testgym")
        self.assertTrue(self.tenant.is_active)
    
    def test_tenant_str(self):
        """Test string representation."""
        expected = "Test Gym (testgym)"
        self.assertEqual(str(self.tenant), expected)


class PlanModelTest(TestCase):
    """Test cases for Plan model."""
    
    def setUp(self):
        """Set up test data."""
        self.plan = Plan.objects.create(
            name="Basic",
            price=99.99,
            billing_cycle="monthly",
            is_public=True
        )
    
    def test_plan_creation(self):
        """Test plan can be created."""
        self.assertEqual(self.plan.name, "Basic")
        self.assertEqual(float(self.plan.price), 99.99)


class TenantReferralAndTrialTest(TestCase):
    """Test cases for tenant onboarding, referrals, and trial expirations."""

    def setUp(self):
        from apps.users.models import User
        self.plan = Plan.objects.create(
            name="Pro Plan",
            price=150.00,
            billing_cycle="monthly",
            is_public=True
        )
        self.referrer = Tenant.objects.create(
            name="Referrer Gym",
            subdomain="referrer",
            is_active=True
        )

    def test_onboard_tenant_with_referral(self):
        from apps.core.tenants.services import TenantAdministrationService
        # Onboard a gym referred by our referrer gym
        referred = TenantAdministrationService.onboard_tenant(
            gym_name="New Referred Gym",
            subdomain="referred",
            owner_email="owner@referred.com",
            owner_password="password123",
            initial_plan_id=self.plan.id,
            referred_by_id=self.referrer.id
        )

        self.assertEqual(referred.referred_by, self.referrer)
        # Check if a referral reward was created (since no trial was specified, should be paid)
        from apps.core.tenants.models import ReferralReward
        reward = ReferralReward.objects.get(referred_tenant=referred)
        self.assertEqual(reward.referrer, self.referrer)
        self.assertEqual(float(reward.reward_amount), 15.00) # 10% of 150
        self.assertEqual(reward.status, 'paid')

    def test_assign_plan_with_trial_referral(self):
        from apps.core.tenants.services import TenantAdministrationService
        # Onboard first (no plan initially)
        referred = TenantAdministrationService.onboard_tenant(
            gym_name="Trial Referred Gym",
            subdomain="trialreferred",
            owner_email="trial@referred.com",
            owner_password="password123",
            referred_by_id=self.referrer.id
        )

        # Assign plan with trial
        TenantAdministrationService.assign_plan(referred, self.plan, trial_days=14)

        # Check if reward is pending because subscription has a trial
        from apps.core.tenants.models import ReferralReward
        reward = ReferralReward.objects.get(referred_tenant=referred)
        self.assertEqual(reward.status, 'pending')
        self.assertEqual(float(reward.reward_amount), 15.00)

    def test_expired_trials_task(self):
        from django.utils import timezone
        from datetime import timedelta
        from apps.core.tenants.models import TenantSubscription
        from apps.core.tasks import check_expired_trials

        # Setup tenant and expired trial subscription
        tenant = Tenant.objects.create(name="Expired Gym", subdomain="expired", is_active=True)
        subscription = TenantSubscription.objects.create(
            tenant=tenant,
            plan=self.plan,
            status='active',
            started_at=timezone.now() - timedelta(days=20),
            trial_ends_at=timezone.now() - timedelta(days=5) # Expired 5 days ago
        )

        # Run task
        result = check_expired_trials()
        self.assertIn("Updated 1 subscriptions", result)

        # Verify status updated
        subscription.refresh_from_db()
        self.assertEqual(subscription.status, 'past_due')


class InventoryPlatformAdminTest(TestCase):
    """Test cases verifying inventory cross-tenant query bypass for Platform Admins."""

    def setUp(self):
        from rest_framework.test import APIClient
        from apps.users.models import User, UserRole
        from apps.scheduling.models import Location
        from apps.inventory.models import Product

        self.client = APIClient()

        # Create two tenants
        self.tenant1 = Tenant.objects.create(name="Gym 1", subdomain="gym1", is_active=True)
        self.tenant2 = Tenant.objects.create(name="Gym 2", subdomain="gym2", is_active=True)

        # Create locations for each
        self.loc1 = Location.objects.create(tenant=self.tenant1, name="Location 1")
        self.loc2 = Location.objects.create(tenant=self.tenant2, name="Location 2")

        # Create products in each tenant
        self.prod1 = Product.objects.create(tenant=self.tenant1, location=self.loc1, name="Product 1", sku="SKU1", price=10.00)
        self.prod2 = Product.objects.create(tenant=self.tenant2, location=self.loc2, name="Product 2", sku="SKU2", price=20.00)

        # Create a Platform Admin user (no tenant_id)
        self.admin = User.objects.create_user(
            email="admin@platform.com",
            password="adminpassword",
            role=UserRole.PLATFORM_ADMIN,
            is_staff=True
        )

        # Create a Gym Owner user for Tenant 1
        self.owner = User.objects.create_user(
            email="owner@gym1.com",
            password="ownerpassword",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant1
        )

    def test_platform_admin_can_see_all_inventory(self):
        # Authenticate as Platform Admin
        self.client.force_authenticate(user=self.admin)

        # Retrieve products
        response = self.client.get('/api/v1/inventory/products/')
        self.assertEqual(response.status_code, 200)
        
        # Admin should see products from BOTH tenants
        product_ids = [item['id'] for item in response.data]
        self.assertIn(str(self.prod1.id), product_ids)
        self.assertIn(str(self.prod2.id), product_ids)

    def test_gym_owner_restricted_to_own_tenant(self):
        # Authenticate as Gym Owner
        self.client.force_authenticate(user=self.owner)

        # Retrieve products passing the gym1 subdomain as Host header
        response = self.client.get('/api/v1/inventory/products/', HTTP_HOST='gym1.testserver')
        self.assertEqual(response.status_code, 200)

        # Owner should see Product 1 but NOT Product 2
        product_ids = [item['id'] for item in response.data]
        self.assertIn(str(self.prod1.id), product_ids)
        self.assertNotIn(str(self.prod2.id), product_ids)


class PlatformLedgerViewSetTest(TestCase):
    """Test cases verifying platform ledgers viewset permissions and cross-tenant querying."""

    def setUp(self):
        from rest_framework.test import APIClient
        from apps.users.models import User, UserRole
        from apps.scheduling.models import Payment
        from apps.payments.models import PlatformLedger

        self.client = APIClient()

        # Create two tenants
        self.tenant1 = Tenant.objects.create(name="Gym 1", subdomain="gym1", is_active=True)
        self.tenant2 = Tenant.objects.create(name="Gym 2", subdomain="gym2", is_active=True)

        # Create member users
        self.member1 = User.objects.create_user(email="member1@gym1.com", password="password123", tenant=self.tenant1)
        self.member2 = User.objects.create_user(email="member2@gym2.com", password="password123", tenant=self.tenant2)

        # Create payments/ledgers in each tenant context
        from apps.core.tenants.context import set_current_tenant
        set_current_tenant(self.tenant1)
        self.pay1 = Payment.objects.create(
            tenant=self.tenant1, client=self.member1, amount=100.00,
            type="package_purchase", status="completed", idempotency_key="key1"
        )

        set_current_tenant(self.tenant2)
        self.pay2 = Payment.objects.create(
            tenant=self.tenant2, client=self.member2, amount=200.00,
            type="package_purchase", status="completed", idempotency_key="key2"
        )
        set_current_tenant(None)

        # Create platform admin
        self.admin = User.objects.create_user(
            email="admin@platform.com",
            password="adminpassword",
            role=UserRole.PLATFORM_ADMIN,
            is_staff=True
        )

    def test_platform_admin_can_view_all_ledgers(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get('/api/v1/payments/platform-ledgers/')
        self.assertEqual(response.status_code, 200)

        ledger_ids = [item['id'] for item in response.data]
        self.assertEqual(len(ledger_ids), 2)


class GymReferralFlowTestCase(APITestCase):
    def setUp(self):
        from apps.users.models import User, UserRole
        # Platform Admin
        self.platform_admin = User.objects.create_user(
            email="platformadmin@fitworks.com",
            password="adminpassword123",
            role=UserRole.PLATFORM_ADMIN,
            is_staff=True,
            tenant=None
        )

        # Referring Gym: La Forza
        self.la_forza = Tenant.objects.create(
            name="La Forza",
            subdomain="la-forza",
            is_active=True
        )
        self.la_forza_owner = User.objects.create_user(
            email="owner@laforza.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.la_forza
        )

        # Modern Billing Plan (Basic Plan - $49.00)
        from apps.payments.models import BillingPlan
        self.basic_billing_plan, _ = BillingPlan.objects.get_or_create(
            slug="basic",
            defaults={"name": "Basic Plan", "is_public": True}
        )

    def test_complete_gym_referral_and_stripe_subscription_flow(self):
        """
        End-to-end verification of gym referral lifecycle:
        1. Tenant onboarding with referring gym (La Forza).
        2. Referral record persisted with pending status and 0 reward before subscription.
        3. Visible in both La Forza's referrals API and Platform Admin Portal.
        4. Stripe checkout completed and fulfilled -> reward becomes paid (10% commission).
        5. Both APIs return the updated paid referral reward and plan name.
        """
        from apps.core.tenants.models import ReferralReward
        from apps.payments.models import TenantBillingSubscription
        from apps.payments.billing_service import FeatureBillingService

        self.client.force_authenticate(user=self.platform_admin)

        # 1. Platform Admin creates new gym "Test Gym Referral" with La Forza as referrer (using name string)
        payload = {
            "gym_name": "Test Gym Referral",
            "subdomain": "test-gym-referral",
            "owner_email": "owner@testgymreferral.com",
            "owner_password": "securepassword123",
            "referred_by": "La Forza"
        }
        create_res = self.client.post('/api/v1/platform/tenants/', payload, format='json')
        self.assertEqual(create_res.status_code, 201)
        new_tenant_id = create_res.data['id']
        new_tenant = Tenant.objects.get(id=new_tenant_id)

        # Verify referral attribution on tenant
        self.assertEqual(new_tenant.referred_by, self.la_forza)

        # 2. Verify pre-subscription ReferralReward was created
        reward = ReferralReward.objects.get(referrer=self.la_forza, referred_tenant=new_tenant)
        self.assertEqual(reward.status, 'pending')
        self.assertEqual(float(reward.reward_amount), 0.00)
        self.assertIsNone(reward.billing_subscription)

        # 3. Check La Forza's referrals API (/api/v1/platform/billing/referrals/)
        self.client.force_authenticate(user=self.la_forza_owner)
        la_forza_res = self.client.get('/api/v1/platform/billing/referrals/')
        self.assertEqual(la_forza_res.status_code, 200)
        self.assertEqual(len(la_forza_res.data), 1)
        ref_data = la_forza_res.data[0]
        self.assertEqual(ref_data['referred_tenant_name'], "Test Gym Referral")
        self.assertEqual(ref_data['status'], "pending")
        self.assertEqual(float(ref_data['reward_amount']), 0.00)
        self.assertEqual(ref_data['plan_name'], "Pending Subscription")

        # Check Platform Admin referrals API (/api/v1/platform/referrals/)
        self.client.force_authenticate(user=self.platform_admin)
        admin_res = self.client.get('/api/v1/platform/referrals/')
        self.assertEqual(admin_res.status_code, 200)
        self.assertTrue(any(r['referred_tenant_name'] == "Test Gym Referral" for r in admin_res.data))

        # 4. Simulate Test Gym Referral purchasing Basic plan via Stripe Checkout
        session_obj = {
            "id": "cs_test_gym_sub_123",
            "subscription": "sub_stripe_123",
            "customer": "cus_stripe_123",
            "amount_total": 4900,  # $49.00 in cents
            "metadata": {
                "tenant_id": str(new_tenant.id),
                "plan_slug": "basic",
                "feature_ids": ""
            }
        }
        billing_sub = FeatureBillingService.fulfill_checkout(session_obj)
        self.assertEqual(billing_sub.status, TenantBillingSubscription.StatusChoices.ACTIVE)

        # 5. Verify ReferralReward updated to paid with 10% commission ($4.90)
        reward.refresh_from_db()
        self.assertEqual(reward.status, 'paid')
        self.assertEqual(float(reward.reward_amount), 4.90)
        self.assertEqual(reward.billing_subscription, billing_sub)

        # 6. Verify APIs return the updated paid reward and plan name
        self.client.force_authenticate(user=self.la_forza_owner)
        la_forza_res2 = self.client.get('/api/v1/platform/billing/referrals/')
        self.assertEqual(la_forza_res2.status_code, 200)
        self.assertEqual(len(la_forza_res2.data), 1)
        ref_data2 = la_forza_res2.data[0]
        self.assertEqual(ref_data2['status'], "paid")
        self.assertEqual(float(ref_data2['reward_amount']), 4.90)
        self.assertEqual(ref_data2['plan_name'], self.basic_billing_plan.name)
        self.assertEqual(ref_data2['subscription_status'], "active")

        # Platform Admin API check
        self.client.force_authenticate(user=self.platform_admin)
        admin_res2 = self.client.get('/api/v1/platform/referrals/')
        self.assertEqual(admin_res2.status_code, 200)
        reward_record = next(r for r in admin_res2.data if r['referred_tenant_name'] == "Test Gym Referral")
        self.assertEqual(reward_record['status'], "paid")
        self.assertEqual(float(reward_record['reward_amount']), 4.90)
        self.assertEqual(reward_record['plan_name'], self.basic_billing_plan.name)

    def test_referral_attribution_with_dict_and_uuid_aliases(self):
        """
        Verify that referred_by can be passed as a dict, UUID string, or alias.
        """
        self.client.force_authenticate(user=self.platform_admin)

        # Test with dict
        res_dict = self.client.post('/api/v1/platform/tenants/', {
            "gym_name": "Gym With Dict Ref",
            "subdomain": "gym-dict-ref",
            "owner_email": "owner@dictref.com",
            "owner_password": "password123",
            "referred_by": {"id": str(self.la_forza.id), "name": "La Forza"}
        }, format='json')
        self.assertEqual(res_dict.status_code, 201)
        tenant_dict = Tenant.objects.get(subdomain="gym-dict-ref")
        self.assertEqual(tenant_dict.referred_by, self.la_forza)

        # Test with UUID string alias referring_gym
        res_alias = self.client.post('/api/v1/platform/tenants/', {
            "gym_name": "Gym With Alias Ref",
            "subdomain": "gym-alias-ref",
            "owner_email": "owner@aliasref.com",
            "owner_password": "password123",
            "referring_gym": str(self.la_forza.id)
        }, format='json')
        self.assertEqual(res_alias.status_code, 201)
        tenant_alias = Tenant.objects.get(subdomain="gym-alias-ref")
        self.assertEqual(tenant_alias.referred_by, self.la_forza)

    def test_self_healing_syncs_existing_referral_without_reward(self):
        """
        Verify that if a tenant has referred_by set in DB without a ReferralReward row,
        calling the referrals API self-heals and creates the record.
        """
        from apps.core.tenants.models import ReferralReward

        orphan_referred = Tenant.objects.create(
            name="Orphan Referred Gym",
            subdomain="orphan-ref",
            is_active=True,
            referred_by=self.la_forza
        )
        self.assertFalse(ReferralReward.objects.filter(referred_tenant=orphan_referred).exists())

        # Calling La Forza's referrals API triggers self-healing
        self.client.force_authenticate(user=self.la_forza_owner)
        res = self.client.get('/api/v1/platform/billing/referrals/')
        self.assertEqual(res.status_code, 200)
        self.assertTrue(any(r['referred_tenant_name'] == "Orphan Referred Gym" for r in res.data))
        self.assertTrue(ReferralReward.objects.filter(referred_tenant=orphan_referred).exists())





