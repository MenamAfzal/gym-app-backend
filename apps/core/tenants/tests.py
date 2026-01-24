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
