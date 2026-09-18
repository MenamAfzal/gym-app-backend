"""
Comprehensive Test Suite for FITV-58 & FITV-143:
Client Referral and Rewards Flow (Option 1: In-App Code Entry & Option 2: Registration / Deep-Link Flow).
"""
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.users.models import User, UserRole, UserProfile, PendingRegistration, EmailOTP, OTPPurpose
from apps.users.services import AuthService
from apps.rewards.models import RewardProgram, RewardRule, RewardWallet, ClientReferral
from apps.rewards.services import RewardWalletService, ClientReferralService


class ReferralEngineComprehensiveTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Tenant 1: Alpha Fitness
        self.tenant1 = Tenant.objects.create(
            name="Alpha Fitness",
            subdomain="alpha-fit",
            is_active=True
        )

        # Tenant 2: Beta Gym (for tenant isolation tests)
        self.tenant2 = Tenant.objects.create(
            name="Beta Gym",
            subdomain="beta-gym",
            is_active=True
        )

        # Setup Reward Programs
        self.program1 = RewardProgram.objects.create(
            tenant=self.tenant1,
            name="Alpha Rewards",
            program_type="hybrid",
            status="active"
        )
        self.program2 = RewardProgram.objects.create(
            tenant=self.tenant2,
            name="Beta Rewards",
            program_type="hybrid",
            status="active"
        )

        # Reward Rules for Tenant 1:
        # Rule 1: Referrer bonus (200 points)
        self.rule_referrer = RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program1,
            name="Referrer Bonus",
            event_type="referral.completed",
            status="active",
            actions=[{"type": "POINTS", "amount": 200, "description": "Referral completed"}]
        )
        # Rule 2: Referee welcome bonus (50 points)
        self.rule_referee = RewardRule.objects.create(
            tenant=self.tenant1,
            program=self.program1,
            name="Referee Welcome Bonus",
            event_type="referral.referee_reward",
            status="active",
            actions=[{"type": "POINTS", "amount": 50, "description": "Joined via referral"}]
        )

        # Users in Tenant 1
        self.client_a = User.objects.create_user(
            email="client_a@alphafit.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant1
        )
        self.wallet_a = RewardWalletService.get_or_create_wallet(tenant_id=self.tenant1.id, user=self.client_a)

        self.client_b = User.objects.create_user(
            email="client_b@alphafit.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant1
        )
        self.wallet_b = RewardWalletService.get_or_create_wallet(tenant_id=self.tenant1.id, user=self.client_b)

        self.client_c = User.objects.create_user(
            email="client_c@alphafit.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant1
        )
        self.wallet_c = RewardWalletService.get_or_create_wallet(tenant_id=self.tenant1.id, user=self.client_c)

        self.owner = User.objects.create_user(
            email="owner@alphafit.com",
            password="Password123!",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant1
        )

        # User in Tenant 2 (Isolation)
        self.client_t2 = User.objects.create_user(
            email="client_t2@betagym.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant2
        )

        set_current_tenant(self.tenant1)

    def tearDown(self):
        set_current_tenant(None)

    # -------------------------------------------------------------------------
    # 1. Profile & Referral Info
    # -------------------------------------------------------------------------
    def test_client_referral_info_endpoint(self):
        """Client can retrieve their referral code, deep link, universal link, and stats."""
        self.client.force_authenticate(user=self.client_a)
        res = self.client.get("/api/v1/rewards/client/referrals/")

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("referral_code", res.data)
        self.assertIn("referral_link", res.data)
        self.assertIn("web_referral_link", res.data)
        self.assertIn("deep_link", res.data)
        self.assertEqual(res.data["total_referrals_completed"], 0)
        self.assertTrue(res.data["deep_link"].startswith("fitverx://join?ref="))

    # -------------------------------------------------------------------------
    # 2. Strict Consent & Security: No Unintended User Creation
    # -------------------------------------------------------------------------
    def test_email_invitation_does_not_create_unregistered_user(self):
        """Submitting an email sends an invite link and NEVER creates an unauthorized user."""
        self.client.force_authenticate(user=self.client_a)
        invitee_email = "nonexistent_friend@example.com"

        self.assertFalse(User.objects.filter(email=invitee_email).exists())

        res = self.client.post(
            "/api/v1/rewards/client/referrals/",
            data={"referee_email": invitee_email},
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "invitation_sent")
        self.assertIn("referral_link", res.data)

        # CRITICAL: No user was created in the database
        self.assertFalse(User.objects.filter(email=invitee_email).exists())
        self.assertFalse(PendingRegistration.objects.filter(email=invitee_email).exists())

    # -------------------------------------------------------------------------
    # 3. Option 1: In-App Referral Code Flow
    # -------------------------------------------------------------------------
    def test_option_1_in_app_code_apply_success(self):
        """Client B enters Client A's code in-app. Both receive reward points."""
        # Get Client A's code
        profile_a = UserProfile.objects.get_or_create(user=self.client_a)[0]
        code_a = profile_a.get_or_create_referral_code()

        # Client B applies code
        self.client.force_authenticate(user=self.client_b)
        res = self.client.post(
            "/api/v1/rewards/client/referrals/apply/",
            data={"referral_code": code_a},
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["status"], "success")
        self.assertEqual(res.data["points_awarded_referrer"], 200)
        self.assertEqual(res.data["points_awarded_referee"], 50)

        # Verify wallet balances
        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, 200)
        self.assertEqual(self.wallet_b.balance, 50)

        # Verify ClientReferral model tracking
        referral = ClientReferral.objects.get(tenant=self.tenant1, referee=self.client_b)
        self.assertEqual(referral.referrer, self.client_a)
        self.assertEqual(referral.status, "COMPLETED")
        self.assertEqual(referral.method, "CODE")
        self.assertEqual(referral.points_awarded_referrer, 200)
        self.assertEqual(referral.points_awarded_referee, 50)

    def test_self_referral_prevented(self):
        """Client cannot use their own referral code."""
        profile_a = UserProfile.objects.get_or_create(user=self.client_a)[0]
        code_a = profile_a.get_or_create_referral_code()

        self.client.force_authenticate(user=self.client_a)
        res = self.client.post(
            "/api/v1/rewards/client/referrals/apply/",
            data={"referral_code": code_a},
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Self-referrals are not permitted", res.data["error"])

        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, 0)

    def test_duplicate_referral_prevented(self):
        """A referee cannot be referred more than once."""
        profile_a = UserProfile.objects.get_or_create(user=self.client_a)[0]
        code_a = profile_a.get_or_create_referral_code()

        profile_c = UserProfile.objects.get_or_create(user=self.client_c)[0]
        code_c = profile_c.get_or_create_referral_code()

        # First referral succeeds
        self.client.force_authenticate(user=self.client_b)
        res1 = self.client.post(
            "/api/v1/rewards/client/referrals/apply/",
            data={"referral_code": code_a},
            format="json"
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # Second referral attempt fails
        res2 = self.client.post(
            "/api/v1/rewards/client/referrals/apply/",
            data={"referral_code": code_c},
            format="json"
        )
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already claimed", res2.data["error"])

    def test_circular_referral_prevented(self):
        """If A referred B, B cannot refer A."""
        profile_a = UserProfile.objects.get_or_create(user=self.client_a)[0]
        code_a = profile_a.get_or_create_referral_code()

        profile_b = UserProfile.objects.get_or_create(user=self.client_b)[0]
        code_b = profile_b.get_or_create_referral_code()

        # A refers B
        self.client.force_authenticate(user=self.client_b)
        res1 = self.client.post(
            "/api/v1/rewards/client/referrals/apply/",
            data={"referral_code": code_a},
            format="json"
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # Now B attempts to refer A
        self.client.force_authenticate(user=self.client_a)
        res2 = self.client.post(
            "/api/v1/rewards/client/referrals/apply/",
            data={"referral_code": code_b},
            format="json"
        )
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Circular referrals", res2.data["error"])

    def test_cross_tenant_referral_prevented(self):
        """Referral codes are strictly tenant-scoped."""
        profile_a = UserProfile.objects.get_or_create(user=self.client_a)[0]
        code_a = profile_a.get_or_create_referral_code()

        # Switch context to Tenant 2
        set_current_tenant(self.tenant2)
        self.client.force_authenticate(user=self.client_t2)

        res = self.client.post(
            "/api/v1/rewards/client/referrals/apply/",
            data={"referral_code": code_a},
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid or unresolvable", res.data["error"])

    def test_non_client_cannot_be_referee(self):
        """Gym Owner / Staff cannot apply referral codes."""
        profile_a = UserProfile.objects.get_or_create(user=self.client_a)[0]
        code_a = profile_a.get_or_create_referral_code()

        self.client.force_authenticate(user=self.owner)
        res = self.client.post(
            "/api/v1/rewards/client/referrals/apply/",
            data={"referral_code": code_a},
            format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("must be a gym client", res.data["error"])

    # -------------------------------------------------------------------------
    # 4. Public Lookup & Deep-Link Redirect Endpoints
    # -------------------------------------------------------------------------
    def test_referral_lookup_endpoint(self):
        """Public endpoint to validate code and return sanitized referrer info."""
        profile_a = UserProfile.objects.get_or_create(user=self.client_a)[0]
        profile_a.first_name = "Alice"
        profile_a.save()
        code_a = profile_a.get_or_create_referral_code()

        # Unauthenticated lookup
        self.client.logout()
        res = self.client.get(f"/api/v1/rewards/referrals/lookup/?ref={code_a}")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data["valid"])
        self.assertEqual(res.data["referral_code"], code_a)
        self.assertEqual(res.data["referrer_name"], "Alice")
        self.assertEqual(res.data["gym_name"], "Alpha Fitness")

        # Invalid code lookup
        res_invalid = self.client.get("/api/v1/rewards/referrals/lookup/?ref=INVALID-CODE")
        self.assertEqual(res_invalid.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(res_invalid.data["valid"])

    def test_referral_join_redirect_endpoint(self):
        """Public universal link redirect handles mobile deep link and web fallback."""
        profile_a = UserProfile.objects.get_or_create(user=self.client_a)[0]
        code_a = profile_a.get_or_create_referral_code()

        self.client.logout()

        # 1. Desktop Browser: Redirects (302) to web registration
        res_desktop = self.client.get(f"/api/v1/rewards/referrals/join/?ref={code_a}")
        self.assertEqual(res_desktop.status_code, status.HTTP_302_FOUND)
        self.assertIn(f"ref={code_a}", res_desktop["Location"])

        # 2. Mobile Browser: Returns 200 HTML with deep link intent and web fallback
        res_mobile = self.client.get(
            f"/api/v1/rewards/referrals/join/?ref={code_a}",
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)"
        )
        self.assertEqual(res_mobile.status_code, status.HTTP_200_OK)
        self.assertEqual(res_mobile["Content-Type"], "text/html")
        html_content = res_mobile.content.decode("utf-8")
        self.assertIn("fitverx://join?ref=", html_content)
        self.assertIn(code_a, html_content)

    # -------------------------------------------------------------------------
    # 5. Option 2: Registration Flow with Referral Code (Direct & OTP)
    # -------------------------------------------------------------------------
    def test_direct_registration_with_referral_code(self):
        """Direct registration with referral_code automatically links referral and awards rewards."""
        profile_a = UserProfile.objects.get_or_create(user=self.client_a)[0]
        code_a = profile_a.get_or_create_referral_code()

        self.client.logout()
        reg_payload = {
            "email": "new_direct_client@alphafit.com",
            "password": "Password123!",
            "first_name": "Dave",
            "last_name": "Direct",
            "role": UserRole.CLIENT,
            "tenant_id": str(self.tenant1.id),
            "referral_code": code_a
        }
        res = self.client.post("/api/v1/users/auth/register/", data=reg_payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # Check new user created
        new_user = User.objects.get(email="new_direct_client@alphafit.com")
        self.assertEqual(new_user.role, UserRole.CLIENT)

        # Verify ClientReferral completed with LINK method
        referral = ClientReferral.objects.get(tenant=self.tenant1, referee=new_user)
        self.assertEqual(referral.referrer, self.client_a)
        self.assertEqual(referral.method, "LINK")
        self.assertEqual(referral.status, "COMPLETED")

        # Verify rewards awarded
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, 200)

        new_wallet = RewardWallet.objects.get(tenant=self.tenant1, user=new_user)
        self.assertEqual(new_wallet.balance, 50)

    def test_otp_registration_preserves_code_and_finalizes_referral(self):
        """OTP flow preserves referral_code in PendingRegistration and completes referral on verification."""
        profile_a = UserProfile.objects.get_or_create(user=self.client_a)[0]
        code_a = profile_a.get_or_create_referral_code()

        otp_email = "new_otp_client@alphafit.com"

        # 1. Initiate registration
        init_payload = {
            "email": otp_email,
            "password": "Password123!",
            "first_name": "Eve",
            "last_name": "Otp",
            "role": UserRole.CLIENT,
            "tenant_id": str(self.tenant1.id),
            "referral_code": code_a
        }
        res_init = self.client.post("/api/v1/users/auth/register/init/", data=init_payload, format="json")
        self.assertEqual(res_init.status_code, status.HTTP_200_OK)

        # Verify PendingRegistration has referral_code
        pending = PendingRegistration.objects.get(email=otp_email)
        self.assertEqual(pending.referral_code, code_a)

        # 2. Mock OTP verification
        otp_record = EmailOTP.objects.filter(email=otp_email, purpose=OTPPurpose.REGISTRATION).latest('created_at')
        fixed_code = "123456"
        otp_record.otp_hash = EmailOTP.hash_code(fixed_code)
        otp_record.save()

        verify_payload = {
            "email": otp_email,
            "code": fixed_code
        }
        res_verify = self.client.post("/api/v1/users/auth/register/verify/", data=verify_payload, format="json")
        self.assertEqual(res_verify.status_code, status.HTTP_201_CREATED)

        # Check new user created
        new_user = User.objects.get(email=otp_email)

        # Verify referral processed
        referral = ClientReferral.objects.get(tenant=self.tenant1, referee=new_user)
        self.assertEqual(referral.referrer, self.client_a)
        self.assertEqual(referral.method, "LINK")
        self.assertEqual(referral.status, "COMPLETED")

        # Verify rewards awarded
        self.wallet_a.refresh_from_db()
        self.assertEqual(self.wallet_a.balance, 200)

        new_wallet = RewardWallet.objects.get(tenant=self.tenant1, user=new_user)
        self.assertEqual(new_wallet.balance, 50)
