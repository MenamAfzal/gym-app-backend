"""
Tenant Model Tests

Basic test scaffolding for tenant models.
"""
from django.test import TestCase
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
        from apps.scheduling.models import Payment, PlatformLedger

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
        response = self.client.get('/api/v1/scheduling/platform-ledgers/')
        self.assertEqual(response.status_code, 200)

        ledger_ids = [item['id'] for item in response.data]
        self.assertEqual(len(ledger_ids), 2)




