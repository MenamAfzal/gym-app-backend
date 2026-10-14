"""
Unit & Integration Tests for B2B Affiliate & Recurring Points System
"""
import uuid
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.users.models import User, UserRole
from apps.core.tenants.models import (
    Tenant, Plan, TenantSubscription, AffiliatePartner, AffiliatePointsTransaction
)
from apps.core.tenants.services import TenantAdministrationService
from apps.core.tenants.affiliate_service import AffiliateService


class AffiliateSystemTests(TestCase):
    """
    End-to-End tests for external affiliate registration, tenant onboarding,
    initial subscription point awards, and recurring renewal points.
    """

    def setUp(self):
        self.client = APIClient()

        # 1. Platform Admin
        self.platform_admin = User.objects.create_superuser(
            email="platform.admin@example.com",
            password="AdminPassword123!",
            first_name="Platform",
            last_name="SuperAdmin"
        )

        # 2. Base Subscription Plan
        self.plan = Plan.objects.create(
            name="Pro Growth Plan",
            price=Decimal("150.00"),
            billing_cycle="monthly",
            is_public=True
        )

    def test_affiliate_registration_success(self):
        """External affiliate registers without owning a gym or having a tenant."""
        payload = {
            "email": "promoter.alex@example.com",
            "password": "SecurePassword123!",
            "first_name": "Alex",
            "last_name": "Consultant",
            "code": "alex_consulting",
            "company_name": "Alex Fitness Advisory",
            "website_or_social": "https://alexfitness.com"
        }
        response = self.client.post(reverse("affiliate-register"), data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("tokens", response.data)
        self.assertIn("affiliate", response.data)
        self.assertEqual(response.data["affiliate"]["code"], "alex_consulting")
        self.assertEqual(response.data["affiliate"]["points_balance"], 0)

        # Verify DB state
        user = User.objects.get(email="promoter.alex@example.com")
        self.assertEqual(user.role, UserRole.AFFILIATE)
        self.assertIsNone(user.tenant)
        self.assertTrue(hasattr(user, "affiliate_partner"))
        self.assertEqual(user.affiliate_partner.code, "alex_consulting")

    def test_affiliate_registration_auto_code_generation(self):
        """If no custom code is provided, system generates a unique AFF-XXXX code."""
        payload = {
            "email": "auto.partner@example.com",
            "password": "SecurePassword123!",
            "first_name": "Partner",
            "last_name": "User"
        }
        response = self.client.post(reverse("affiliate-register"), data=payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        code = response.data["affiliate"]["code"]
        self.assertTrue(code.startswith("AFF-"))

    def test_affiliate_registration_duplicate_email_fails(self):
        """Cannot register with an email already in use."""
        AffiliateService.register_affiliate(
            email="existing@example.com",
            password="Password123!",
            code="existing_code"
        )
        payload = {
            "email": "existing@example.com",
            "password": "Password123!",
            "code": "different_code"
        }
        response = self.client.post(reverse("affiliate-register"), data=payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_affiliate_registration_duplicate_code_fails(self):
        """Cannot register with a referral code already taken."""
        AffiliateService.register_affiliate(
            email="partner1@example.com",
            password="Password123!",
            code="unique_code"
        )
        payload = {
            "email": "partner2@example.com",
            "password": "Password123!",
            "code": "unique_code"
        }
        response = self.client.post(reverse("affiliate-register"), data=payload)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_affiliate_resolve_endpoint(self):
        """Public endpoint verifies valid vs invalid affiliate codes."""
        _, partner = AffiliateService.register_affiliate(
            email="resolve.partner@example.com",
            password="Password123!",
            code="power_gyms",
            company_name="Power Gym Promos"
        )

        # 1. Valid code
        res_valid = self.client.get(reverse("affiliate-resolve"), {"code": "power_gyms"})
        self.assertEqual(res_valid.status_code, status.HTTP_200_OK)
        self.assertTrue(res_valid.data["valid"])
        self.assertEqual(res_valid.data["code"], "power_gyms")
        self.assertEqual(res_valid.data["company_name"], "Power Gym Promos")

        # 2. Case-insensitive match
        res_case = self.client.get(reverse("affiliate-resolve"), {"code": "POWER_GYMS"})
        self.assertEqual(res_case.status_code, status.HTTP_200_OK)
        self.assertTrue(res_case.data["valid"])

        # 3. Invalid code
        res_invalid = self.client.get(reverse("affiliate-resolve"), {"code": "non_existent"})
        self.assertEqual(res_invalid.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res_invalid.data["valid"])

    def test_tenant_onboarding_links_affiliate_and_awards_initial_points(self):
        """Onboarding a gym with affiliate_code awards initial subscription points."""
        _, partner = AffiliateService.register_affiliate(
            email="influencer@example.com",
            password="Password123!",
            code="apex_affiliate"
        )
        self.assertEqual(partner.points_balance, 0)

        # Onboard gym tenant referred by affiliate
        tenant = TenantAdministrationService.onboard_tenant(
            gym_name="Apex Fitness Club",
            subdomain="apex-fitness-test",
            owner_email="owner@apexfitness.com",
            owner_password="SecurePassword123!",
            initial_plan_id=self.plan.id,
            affiliate_code="apex_affiliate"
        )

        self.assertIsNotNone(tenant.affiliate)
        self.assertEqual(tenant.affiliate.id, partner.id)

        # Partner should have received 500 initial points
        partner.refresh_from_db()
        self.assertEqual(partner.points_balance, 500)
        self.assertEqual(partner.lifetime_points_earned, 500)

        # Verify transaction ledger
        tx = AffiliatePointsTransaction.objects.get(
            affiliate=partner,
            tenant=tenant,
            transaction_type=AffiliatePointsTransaction.TransactionType.INITIAL_SUBSCRIPTION
        )
        self.assertEqual(tx.points, 500)
        self.assertIn("Initial subscription for Apex Fitness Club", tx.description)

        # Guard check: Calling initial award again does NOT duplicate points
        AffiliateService.award_initial_subscription_points(tenant)
        partner.refresh_from_db()
        self.assertEqual(partner.points_balance, 500)

    def test_subscription_renewal_recurring_points(self):
        """Subscription renewals award recurring points (+100) on each renewal event."""
        _, partner = AffiliateService.register_affiliate(
            email="recurring.affiliate@example.com",
            password="Password123!",
            code="monthly_bounty"
        )

        tenant = TenantAdministrationService.onboard_tenant(
            gym_name="Metro Gym",
            subdomain="metro-gym-test",
            owner_email="owner@metrogym.com",
            owner_password="SecurePassword123!",
            initial_plan_id=self.plan.id,
            affiliate_code="monthly_bounty"
        )
        partner.refresh_from_db()
        self.assertEqual(partner.points_balance, 500)

        subscription = tenant.subscriptions.filter(status="active").first()

        # Month 1 Renewal
        tx_month1 = AffiliateService.award_renewal_subscription_points(
            tenant=tenant,
            subscription=subscription,
            invoice_id="in_test_month_1"
        )
        self.assertIsNotNone(tx_month1)
        self.assertEqual(tx_month1.points, 100)
        partner.refresh_from_db()
        self.assertEqual(partner.points_balance, 600)
        self.assertEqual(partner.lifetime_points_earned, 600)

        # Deduplication check: Same invoice should NOT award points again
        duplicate_tx = AffiliateService.award_renewal_subscription_points(
            tenant=tenant,
            subscription=subscription,
            invoice_id="in_test_month_1"
        )
        self.assertIsNone(duplicate_tx)
        partner.refresh_from_db()
        self.assertEqual(partner.points_balance, 600)

        # Month 2 Renewal (new invoice)
        tx_month2 = AffiliateService.award_renewal_subscription_points(
            tenant=tenant,
            subscription=subscription,
            invoice_id="in_test_month_2"
        )
        self.assertIsNotNone(tx_month2)
        partner.refresh_from_db()
        self.assertEqual(partner.points_balance, 700)
        self.assertEqual(partner.lifetime_points_earned, 700)

    def test_affiliate_self_service_dashboard_and_history(self):
        """Affiliate can view their dashboard stats, points ledger, and referred gyms."""
        user, partner = AffiliateService.register_affiliate(
            email="dashboard.partner@example.com",
            password="Password123!",
            code="dashboard_code",
            company_name="Gym Growth Agency"
        )

        tenant = TenantAdministrationService.onboard_tenant(
            gym_name="Studio One",
            subdomain="studio-one-test",
            owner_email="owner@studioone.com",
            owner_password="SecurePassword123!",
            initial_plan_id=self.plan.id,
            affiliate_code="dashboard_code"
        )

        # Authenticate as affiliate
        self.client.force_authenticate(user=user)

        # 1. Dashboard
        res_dash = self.client.get(reverse("affiliate-dashboard"))
        self.assertEqual(res_dash.status_code, status.HTTP_200_OK)
        self.assertEqual(res_dash.data["points_balance"], 500)
        self.assertEqual(res_dash.data["total_gyms_referred"], 1)
        self.assertEqual(res_dash.data["active_gyms_referred"], 1)
        self.assertEqual(len(res_dash.data["recent_transactions"]), 1)

        # 2. History
        res_hist = self.client.get(reverse("affiliate-history"))
        self.assertEqual(res_hist.status_code, status.HTTP_200_OK)
        self.assertEqual(res_hist.data["count"], 1)
        self.assertEqual(res_hist.data["results"][0]["points"], 500)

        # 3. Gyms
        res_gyms = self.client.get(reverse("affiliate-gyms"))
        self.assertEqual(res_gyms.status_code, status.HTTP_200_OK)
        self.assertEqual(res_gyms.data["count"], 1)
        self.assertEqual(res_gyms.data["results"][0]["name"], "Studio One")

    def test_platform_admin_affiliate_management_and_adjust_points(self):
        """Platform Admin can audit affiliates and manually adjust points."""
        user, partner = AffiliateService.register_affiliate(
            email="managed.affiliate@example.com",
            password="Password123!",
            code="managed_code"
        )

        # Authenticate as Platform Admin
        self.client.force_authenticate(user=self.platform_admin)

        # 1. List affiliates
        res_list = self.client.get(reverse("platform-affiliates-list"))
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(res_list.data["count"], 1)

        # 2. Adjust points (+250 bonus points)
        url_adjust = reverse("platform-affiliates-adjust-points", kwargs={"pk": partner.id})
        res_adjust = self.client.post(url_adjust, data={"points": 250, "reason": "Bonus for conference promotion"})
        self.assertEqual(res_adjust.status_code, status.HTTP_200_OK)
        self.assertEqual(res_adjust.data["points_balance"], 250)

        partner.refresh_from_db()
        self.assertEqual(partner.points_balance, 250)

        # 3. Negative deduction
        res_deduct = self.client.post(url_adjust, data={"points": -50, "reason": "Correction"})
        self.assertEqual(res_deduct.status_code, status.HTTP_200_OK)
        self.assertEqual(res_deduct.data["points_balance"], 200)

        # 4. Excessive deduction below 0 fails
        res_fail = self.client.post(url_adjust, data={"points": -500, "reason": "Excessive deduction"})
        self.assertEqual(res_fail.status_code, status.HTTP_400_BAD_REQUEST)
