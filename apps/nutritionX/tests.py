from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from apps.core.tenants.models import Tenant
from apps.scheduling.models import TenantBookingSettings
from apps.users.models import User, UserProfile, UserRole, GenderChoices
from apps.users.services import UserService
from apps.nutritionX.models import NutritionGoal
from apps.food_logger.models import UserNutritionGoal


class DefaultNutritionGoalsAndSchedulingFlagTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(
            name="Peak Fitness",
            subdomain="peak-fitness"
        )
        self.owner = UserService.create_user_with_profile(
            email="owner@peakfitness.com",
            password="Password123!",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant,
            gender=GenderChoices.MALE
        )

    def test_is_available_for_scheduling_defaults_to_false_and_can_be_toggled(self):
        """Verify is_available_for_scheduling defaults to False and syncs across Tenant and BookingSettings."""
        self.assertFalse(self.tenant.is_available_for_scheduling)
        settings_obj = TenantBookingSettings.get_or_create_for_tenant(self.tenant)
        self.assertFalse(settings_obj.is_available_for_scheduling)

        # Authenticate as gym owner and check /api/v1/scheduling/booking-settings/
        self.client.force_authenticate(user=self.owner)
        get_resp = self.client.get("/api/v1/scheduling/booking-settings/")
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(get_resp.data["is_available_for_scheduling"])

        # Toggle via PATCH /api/v1/scheduling/booking-settings/
        patch_resp = self.client.patch(
            "/api/v1/scheduling/booking-settings/",
            {"is_available_for_scheduling": True},
            format="json"
        )
        self.assertEqual(patch_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(patch_resp.data["is_available_for_scheduling"])

        self.tenant.refresh_from_db()
        self.assertTrue(self.tenant.is_available_for_scheduling)

        # Check profile/me response includes is_available_for_scheduling
        me_resp = self.client.get("/api/v1/users/profiles/me/")
        self.assertEqual(me_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(me_resp.data["is_available_for_scheduling"])

    def test_male_and_female_default_goals_seeded_on_user_creation(self):
        """Verify male and female users automatically receive their respective default nutrition goals."""
        male_client = UserService.create_user_with_profile(
            email="male_client@peakfitness.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            gender=GenderChoices.MALE
        )
        female_client = UserService.create_user_with_profile(
            email="female_client@peakfitness.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            gender=GenderChoices.FEMALE
        )

        male_nx = NutritionGoal.all_objects.filter(user=male_client, is_active=True).first()
        self.assertIsNotNone(male_nx)
        self.assertEqual(float(male_nx.calories_goal_kcal), 2500.0)
        self.assertEqual(float(male_nx.protein_goal_g), 160.0)
        self.assertEqual(float(male_nx.carbs_goal_g), 280.0)
        self.assertEqual(float(male_nx.fat_goal_g), 80.0)
        self.assertEqual(float(male_nx.water_intake_goal_ml), 3000.0)

        female_nx = NutritionGoal.all_objects.filter(user=female_client, is_active=True).first()
        self.assertIsNotNone(female_nx)
        self.assertEqual(float(female_nx.calories_goal_kcal), 2000.0)
        self.assertEqual(float(female_nx.protein_goal_g), 130.0)
        self.assertEqual(float(female_nx.carbs_goal_g), 220.0)
        self.assertEqual(float(female_nx.fat_goal_g), 65.0)
        self.assertEqual(float(female_nx.water_intake_goal_ml), 2200.0)

        male_fl = UserNutritionGoal.objects.filter(user=male_client).first()
        self.assertIsNotNone(male_fl)
        self.assertEqual(male_fl.daily_calories, 2500.0)

        female_fl = UserNutritionGoal.objects.filter(user=female_client).first()
        self.assertIsNotNone(female_fl)
        self.assertEqual(female_fl.daily_calories, 2000.0)

    def test_lazy_seeding_for_existing_users_without_goals_and_gender_switch(self):
        """Verify existing users without goals get default goals on GET and switching gender updates unmodified default goals."""
        user = User.objects.create_user(
            email="existing@peakfitness.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        UserProfile.objects.create(user=user, gender=GenderChoices.FEMALE)

        self.client.force_authenticate(user=user)

        # GET /api/v1/nutrition/goals/client/ should auto-create female default instead of 404
        resp = self.client.get("/api/v1/nutrition/goals/client/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(float(resp.data["calories_goal_kcal"]), 2000.0)

        # Update user gender to male via PATCH /api/v1/users/profiles/me/
        update_resp = self.client.patch(
            "/api/v1/users/profiles/me/",
            {"gender": "male"},
            format="json"
        )
        self.assertEqual(update_resp.status_code, status.HTTP_200_OK)

        # Active goal should now reflect male defaults
        resp_after = self.client.get("/api/v1/nutrition/goals/client/")
        self.assertEqual(resp_after.status_code, status.HTTP_200_OK)
        self.assertEqual(float(resp_after.data["calories_goal_kcal"]), 2500.0)
        self.assertEqual(float(resp_after.data["protein_goal_g"]), 160.0)

        # Check defaults endpoint
        defaults_resp = self.client.get("/api/v1/nutrition/goals/defaults/")
        self.assertEqual(defaults_resp.status_code, status.HTTP_200_OK)
        self.assertIn("male", defaults_resp.data)
        self.assertIn("female", defaults_resp.data)
        self.assertEqual(defaults_resp.data["user_gender"], "male")
