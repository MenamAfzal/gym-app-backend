"""
Unit & Integration Tests for B2B Affiliate & Recurring Points System
"""
import uuid
import json
from decimal import Decimal
from unittest.mock import patch
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

        # 3. Invalid code returns 400 Bad Request with distinguishable error code
        res_invalid = self.client.get(reverse("affiliate-resolve"), {"code": "non_existent"})
        self.assertEqual(res_invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(res_invalid.data["valid"])
        self.assertEqual(res_invalid.data["error"], "invalid_code")
        self.assertIn("invalid", res_invalid.data["detail"].lower())

        # 4. Inactive code returns 400 Bad Request with distinguishable error code
        partner.is_active = False
        partner.save()
        res_inactive = self.client.get(reverse("affiliate-resolve"), {"code": "power_gyms"})
        self.assertEqual(res_inactive.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(res_inactive.data["valid"])
        self.assertEqual(res_inactive.data["error"], "inactive_code")
        self.assertIn("inactive", res_inactive.data["detail"].lower())

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

    def test_tenant_detail_api_returns_gym_and_affiliate_referral_info(self):
        """
        Platform Admin tenant detail and analytics APIs return referred_by info for both:
        - Gym referring another gym (type: 'gym')
        - Affiliate partner referring a gym (type: 'affiliate')
        """
        # Create Referring Gym
        referring_gym = Tenant.objects.create(name="Parent Gym Alpha", subdomain="parent-alpha")

        # Create Gym referred by referring_gym
        gym_referred_by_gym = Tenant.objects.create(
            name="Child Gym Beta",
            subdomain="child-beta",
            referred_by=referring_gym
        )

        # Create Affiliate Partner
        _, partner = AffiliateService.register_affiliate(
            email="partner.vip@example.com",
            password="Password123!",
            code="vip_partner",
            company_name="VIP Sports Marketing"
        )

        # Create Gym referred by affiliate partner
        gym_referred_by_affiliate = Tenant.objects.create(
            name="Affiliate Gym Gamma",
            subdomain="affiliate-gamma",
            affiliate=partner
        )

        # Authenticate as Platform Admin
        self.client.force_authenticate(user=self.platform_admin)

        # 1. Retrieve gym referred by gym
        url_gym_ref = reverse("platform-tenants-detail", kwargs={"pk": gym_referred_by_gym.id})
        res_gym_ref = self.client.get(url_gym_ref)
        self.assertEqual(res_gym_ref.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res_gym_ref.data["referred_by"])
        self.assertEqual(res_gym_ref.data["referred_by"]["type"], "gym")
        self.assertEqual(res_gym_ref.data["referred_by"]["id"], str(referring_gym.id))
        self.assertEqual(res_gym_ref.data["referred_by"]["name"], "Parent Gym Alpha")
        self.assertEqual(res_gym_ref.data["referred_by"]["subdomain"], "parent-alpha")

        # 2. Retrieve gym referred by affiliate
        url_aff_ref = reverse("platform-tenants-detail", kwargs={"pk": gym_referred_by_affiliate.id})
        res_aff_ref = self.client.get(url_aff_ref)
        self.assertEqual(res_aff_ref.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res_aff_ref.data["referred_by"])
        self.assertEqual(res_aff_ref.data["referred_by"]["type"], "affiliate")
        self.assertEqual(res_aff_ref.data["referred_by"]["id"], str(partner.id))
        self.assertEqual(res_aff_ref.data["referred_by"]["code"], "vip_partner")
        self.assertEqual(res_aff_ref.data["referred_by"]["company_name"], "VIP Sports Marketing")
        self.assertEqual(res_aff_ref.data["referred_by"]["email"], "partner.vip@example.com")
        self.assertEqual(res_aff_ref.data["referred_by"]["name"], "VIP Sports Marketing")

        # 3. Analytics List API
        url_analytics = reverse("tenant-analytics")
        res_analytics = self.client.get(url_analytics)
        self.assertEqual(res_analytics.status_code, status.HTTP_200_OK)

        results_by_id = {item["tenant_id"]: item for item in res_analytics.data["results"]}
        self.assertEqual(results_by_id[str(gym_referred_by_gym.id)]["referred_by"]["type"], "gym")
        self.assertEqual(results_by_id[str(gym_referred_by_affiliate.id)]["referred_by"]["type"], "affiliate")
        self.assertEqual(results_by_id[str(gym_referred_by_affiliate.id)]["referred_by"]["code"], "vip_partner")

    def test_onboarding_affiliate_validation_errors(self):
        """Platform Admin tenant creation fails with 400 Bad Request if affiliate code is invalid or inactive."""
        _, inactive_partner = AffiliateService.register_affiliate(
            email="inactive.partner@example.com",
            password="Password123!",
            code="dormant_code"
        )
        inactive_partner.is_active = False
        inactive_partner.save()

        self.client.force_authenticate(user=self.platform_admin)

        # 1. Invalid non-existent code
        payload_invalid = {
            "gym_name": "Invalid Code Gym",
            "subdomain": "invalid-code-gym",
            "owner_email": "owner.invalid@example.com",
            "owner_password": "SecurePassword123!",
            "affiliate_code": "non_existent_xyz"
        }
        res_invalid = self.client.post(reverse("platform-tenants-list"), data=payload_invalid)
        self.assertEqual(res_invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("affiliate_code", str(res_invalid.data).lower())

        # 2. Inactive code
        payload_inactive = {
            "gym_name": "Inactive Code Gym",
            "subdomain": "inactive-code-gym",
            "owner_email": "owner.inactive@example.com",
            "owner_password": "SecurePassword123!",
            "affiliate_code": "dormant_code"
        }
        res_inactive = self.client.post(reverse("platform-tenants-list"), data=payload_inactive)
        self.assertEqual(res_inactive.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("inactive", str(res_inactive.data).lower())

    def test_affiliate_referral_link_functional(self):
        """
        Shared affiliate referral link (/join-gym?ref=CODE) opens landing flow.
        - Browser request returns 200 with HTML landing page.
        - API request returns 200 with JSON resolution & onboarding URLs.
        """
        _, partner = AffiliateService.register_affiliate(
            email="promoter.landing@example.com",
            password="Password123!",
            code="fitness_pro",
            company_name="Fitness Pro Partners"
        )

        # 1. Browser HTML request
        res_html = self.client.get(
            reverse("root-affiliate-join-landing"),
            {"ref": "fitness_pro"},
            HTTP_ACCEPT="text/html,application/xhtml+xml,application/xml"
        )
        self.assertEqual(res_html.status_code, status.HTTP_200_OK)
        self.assertIn("text/html", res_html["Content-Type"])
        self.assertIn("Fitness Pro Partners", res_html.content.decode())
        self.assertIn("fitness_pro", res_html.content.decode())
        self.assertIn("Get Started & Register Gym", res_html.content.decode())

        # 2. API JSON request
        res_json = self.client.get(
            reverse("root-affiliate-join-landing"),
            {"ref": "fitness_pro"},
            HTTP_ACCEPT="application/json"
        )
        self.assertEqual(res_json.status_code, status.HTTP_200_OK)
        self.assertTrue(res_json.data["valid"])
        self.assertEqual(res_json.data["affiliate_code"], "fitness_pro")
        self.assertEqual(res_json.data["company_name"], "Fitness Pro Partners")
        self.assertIn("register?ref=fitness_pro", res_json.data["onboarding_url"])

    @patch("stripe.Webhook.construct_event")
    def test_stripe_checkout_and_renewal_awards_affiliate_points(self, mock_construct_event):
        """
        Full flow:
        1. Onboard gym with affiliate code.
        2. Fulfill Stripe checkout -> initial 500 points awarded to affiliate.
        3. Webhook invoice payment succeeded (renewal) -> 100 renewal points awarded.
        """
        from apps.payments.models import BillingPlan, TenantBillingSubscription
        from apps.payments.billing_service import FeatureBillingService

        # 1. Register Affiliate
        _, partner = AffiliateService.register_affiliate(
            email="b2b.partner@example.com",
            password="Password123!",
            code="b2b_growth",
            company_name="B2B Growth Hub"
        )
        self.assertEqual(partner.points_balance, 0)

        # 2. Create BillingPlan
        billing_plan, _ = BillingPlan.objects.get_or_create(
            slug=BillingPlan.PlanSlug.PREMIUM,
            defaults={
                "name": "Premium Plan",
                "allowed_feature_count": 5,
                "is_public": True
            }
        )

        # 3. Onboard Tenant with affiliate code
        tenant = TenantAdministrationService.onboard_tenant(
            gym_name="Titan Athletics",
            subdomain="titan-athletics-test",
            owner_email="titan.owner@example.com",
            owner_password="Password123!",
            affiliate_code="b2b_growth"
        )
        self.assertEqual(tenant.affiliate, partner)

        # 4. Fulfill Stripe Checkout
        session_dict = {
            "id": "cs_test_titan_subscription",
            "subscription": "sub_stripe_titan_123",
            "customer": "cus_stripe_titan_123",
            "amount_total": 15000,
            "metadata": {
                "tenant_id": str(tenant.id),
                "plan_slug": billing_plan.slug,
            }
        }
        billing_sub = FeatureBillingService.fulfill_checkout(session_dict)
        self.assertEqual(billing_sub.status, TenantBillingSubscription.StatusChoices.ACTIVE)

        # Verify initial points awarded
        partner.refresh_from_db()
        self.assertEqual(partner.points_balance, 500)
        self.assertEqual(partner.lifetime_points_earned, 500)

        # Verify initial transaction record
        tx_initial = AffiliatePointsTransaction.objects.filter(
            affiliate=partner,
            tenant=tenant,
            transaction_type=AffiliatePointsTransaction.TransactionType.INITIAL_SUBSCRIPTION
        ).first()
        self.assertIsNotNone(tx_initial)
        self.assertEqual(tx_initial.points, 500)

        # Guard: re-triggering fulfill_checkout does not double-award initial points
        FeatureBillingService.fulfill_checkout(session_dict)
        partner.refresh_from_db()
        self.assertEqual(partner.points_balance, 500)

        # 5. Trigger renewal invoice payment
        invoice_event_data = {
            "id": "in_stripe_titan_cycle_1",
            "subscription": "sub_stripe_titan_123",
            "billing_reason": "subscription_cycle",
            "amount_paid": 15000,
            "status": "paid"
        }
        event_payload = {
            "id": "evt_test_titan_renewal",
            "type": "invoice.payment_succeeded",
            "data": {"object": invoice_event_data}
        }
        mock_construct_event.return_value = event_payload

        url_webhook = reverse("stripe-checkout-webhook")
        res_webhook = self.client.post(
            url_webhook,
            data=json.dumps(event_payload),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=123,v1=mock_signature"
        )
        self.assertEqual(res_webhook.status_code, 200)

        # Verify renewal points awarded (+100)
        partner.refresh_from_db()
        self.assertEqual(partner.points_balance, 600)
        self.assertEqual(partner.lifetime_points_earned, 600)

        tx_renewal = AffiliatePointsTransaction.objects.filter(
            affiliate=partner,
            tenant=tenant,
            transaction_type=AffiliatePointsTransaction.TransactionType.SUBSCRIPTION_RENEWAL
        ).first()
        self.assertIsNotNone(tx_renewal)
        self.assertEqual(tx_renewal.points, 100)
        self.assertEqual(tx_renewal.metadata.get("invoice_id"), "in_stripe_titan_cycle_1")
