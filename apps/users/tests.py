from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from apps.users.models import User, UserRole
from apps.core.tenants.models import Tenant
from apps.scheduling.models import Location, ClassTemplate, ClassSession, Booking, Package, PackageType
from django.utils import timezone
from datetime import timedelta

class ClientDetailedSchedulingAPITest(TestCase):
    def setUp(self):
        # Create tenant
        self.tenant = Tenant.objects.create(name="Padel Gym", subdomain="padel")
        
        # Create Gym Owner
        self.owner = User.objects.create_user(
            email="owner@padel.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )
        
        # Create Client
        self.client_user = User.objects.create_user(
            email="client@padel.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        
        # Setup API Client
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.owner)

    def test_clients_detailed_scheduling(self):
        # Create a location
        location = Location.objects.create(
            tenant=self.tenant,
            name="Downtown Studio",
            address="123 Street",
            timezone="America/New_York"
        )
        
        # Create class template
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=location,
            name="Power Pilates",
            duration_min=60
        )
        
        # Create a past and an upcoming class session
        now = timezone.now()
        past_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            start_at=now - timedelta(days=2),
            end_at=now - timedelta(days=2, hours=-1),
            capacity=10,
            status="scheduled"
        )
        
        upcoming_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=-1),
            capacity=10,
            status="scheduled"
        )
        
        # Create PackageType and assign Package to client
        pkg_type = PackageType.objects.create(
            tenant=self.tenant,
            location=location,
            name="Standard 5-Pack",
            credit_count=5,
            price="100.00",
            validity_days=30
        )
        
        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            package_type=pkg_type,
            credits_remaining=5,
            expires_at=now + timedelta(days=30)
        )
        
        # Bookings for client
        past_booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=past_session,
            credit_source=pkg,
            status="attended"
        )
        
        upcoming_booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=upcoming_session,
            credit_source=pkg,
            status="booked"
        )
        
        # Request API with subdomain HTTP_HOST header
        url = reverse('users-clients-detailed-scheduling')
        response = self.api_client.get(url, HTTP_HOST='padel.testserver')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Check pagination fields
        self.assertIn('count', response.data)
        self.assertIn('results', response.data)
        
        results = response.data['results']
        self.assertEqual(len(results), 1)
        client_data = results[0]
        
        self.assertEqual(client_data['email'], self.client_user.email)
        
        # Verify stats
        stats = client_data['stats']
        self.assertEqual(stats['total_classes_booked'], 1)
        self.assertEqual(stats['total_classes_attended'], 1)
        self.assertEqual(stats['total_packages_purchased'], 1)
        
        # Verify next/previous class session info
        self.assertIsNotNone(client_data['next_class_session'])
        self.assertEqual(client_data['next_class_session']['booking_id'], str(upcoming_booking.id))
        self.assertEqual(client_data['next_class_session']['class_name'], "Power Pilates")
        self.assertEqual(client_data['next_class_session']['status'], "booked")
        
        self.assertIsNotNone(client_data['previous_class_session'])
        self.assertEqual(client_data['previous_class_session']['booking_id'], str(past_booking.id))
        self.assertEqual(client_data['previous_class_session']['class_name'], "Power Pilates")
        self.assertEqual(client_data['previous_class_session']['status'], "attended")

        # Test Query by specific ID parameter
        response_id = self.api_client.get(url + f"?id={self.client_user.id}", HTTP_HOST='padel.testserver')
        self.assertEqual(response_id.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_id.data['results']), 1)

        # Test single detail URL
        detail_url = reverse('users-detailed-scheduling', kwargs={'pk': self.client_user.id})
        response_detail = self.api_client.get(detail_url, HTTP_HOST='padel.testserver')
        self.assertEqual(response_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(response_detail.data['id'], str(self.client_user.id))
        self.assertEqual(response_detail.data['stats']['total_classes_booked'], 1)


class StaffDetailedSchedulingAPITest(TestCase):
    def setUp(self):
        # Create tenant
        self.tenant = Tenant.objects.create(name="Padel Gym", subdomain="padel")
        
        # Create Gym Owner
        self.owner = User.objects.create_user(
            email="owner@padel.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )
        
        # Create Trainer
        self.trainer = User.objects.create_user(
            email="trainer@padel.com",
            password="password123",
            role=UserRole.TRAINER,
            tenant=self.tenant
        )
        
        # Create Client
        self.client_user = User.objects.create_user(
            email="client@padel.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        
        # Setup API Client
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.owner)

    def test_staff_detailed_scheduling(self):
        from apps.scheduling.models import StaffLocation, StaffAvailability, StaffClientAssignment, Appointment, SubstituteRequest
        
        # Create a location
        location = Location.objects.create(
            tenant=self.tenant,
            name="Downtown Studio",
            address="123 Street",
            timezone="America/New_York"
        )
        
        # Map staff to location
        StaffLocation.objects.create(
            tenant=self.tenant,
            staff=self.trainer,
            location=location
        )
        
        # Define availability
        StaffAvailability.objects.create(
            tenant=self.tenant,
            staff=self.trainer,
            weekday_or_date="monday",
            start_time="08:00:00",
            end_time="17:00:00",
            is_blackout=False
        )
        
        # Assign client
        StaffClientAssignment.objects.create(
            tenant=self.tenant,
            staff=self.trainer,
            client=self.client_user
        )
        
        # Create template & sessions
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=location,
            name="Power Pilates",
            duration_min=60
        )
        
        now = timezone.now()
        past_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            staff=self.trainer,
            start_at=now - timedelta(days=2),
            end_at=now - timedelta(days=2, hours=-1),
            capacity=10,
            status="scheduled"
        )
        
        upcoming_session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            staff=self.trainer,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=-1),
            capacity=10,
            status="scheduled"
        )
        
        # Private Appointment
        appt = Appointment.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            provider=self.trainer,
            location=location,
            start_at=now + timedelta(days=3),
            end_at=now + timedelta(days=3, hours=-1),
            status="scheduled"
        )
        
        # Substitute request
        sub_req = SubstituteRequest.objects.create(
            tenant=self.tenant,
            session=past_session,
            requested_by_staff=self.trainer,
            status="open"
        )
        
        # Request API
        url = reverse('users-staff-detailed-scheduling')
        response = self.api_client.get(url, HTTP_HOST='padel.testserver')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('count', response.data)
        
        results = response.data['results']
        # Gym owner and Trainer are both staff (non-client)
        self.assertEqual(len(results), 2)
        
        # Find trainer's data
        trainer_data = next(item for item in results if item['id'] == str(self.trainer.id))
        self.assertEqual(trainer_data['email'], self.trainer.email)
        
        # Verify stats
        stats = trainer_data['stats']
        self.assertEqual(stats['total_upcoming_classes'], 1)
        self.assertEqual(stats['total_past_classes'], 1)
        self.assertEqual(stats['total_hours_taught'], 1.0)
        self.assertEqual(stats['total_private_appointments'], 1)
        self.assertEqual(stats['total_assigned_clients'], 1)
        self.assertEqual(stats['total_substitute_requests_raised'], 1)
        
        # Verify location, availability, assignments, next/prev pointers
        self.assertEqual(len(trainer_data['locations']), 1)
        self.assertEqual(trainer_data['locations'][0]['name'], "Downtown Studio")
        
        self.assertEqual(len(trainer_data['availabilities']), 1)
        self.assertEqual(trainer_data['availabilities'][0]['weekday_or_date'], "monday")
        
        self.assertEqual(len(trainer_data['assigned_clients']), 1)
        self.assertEqual(trainer_data['assigned_clients'][0]['client_email'], self.client_user.email)
        
        self.assertIsNotNone(trainer_data['next_class_session'])
        self.assertEqual(trainer_data['next_class_session']['session_id'], str(upcoming_session.id))
        
        self.assertIsNotNone(trainer_data['previous_class_session'])
        self.assertEqual(trainer_data['previous_class_session']['session_id'], str(past_session.id))
        
        self.assertIsNotNone(trainer_data['next_appointment'])
        self.assertEqual(trainer_data['next_appointment']['appointment_id'], str(appt.id))
        
        self.assertEqual(len(trainer_data['substitute_requests_raised']), 1)
        self.assertEqual(trainer_data['substitute_requests_raised'][0]['id'], str(sub_req.id))

        # Test Query by specific ID parameter
        response_id = self.api_client.get(url + f"?id={self.trainer.id}", HTTP_HOST='padel.testserver')
        self.assertEqual(response_id.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_id.data['results']), 1)

        # Test single detail URL
        detail_url = reverse('users-detailed-scheduling', kwargs={'pk': self.trainer.id})
        response_detail = self.api_client.get(detail_url, HTTP_HOST='padel.testserver')
        self.assertEqual(response_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(response_detail.data['id'], str(self.trainer.id))
        self.assertEqual(response_detail.data['stats']['total_upcoming_classes'], 1)

    def test_user_deactivate_toggle(self):
        from apps.scheduling.models import Booking, Package, PackageType
        
        # Create client booking to cancel
        location = Location.objects.create(
            tenant=self.tenant,
            name="Downtown Studio",
            address="123 Street",
            timezone="America/New_York"
        )
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=location,
            name="Power Pilates",
            duration_min=60
        )
        now = timezone.now()
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            start_at=now + timedelta(days=2),
            end_at=now + timedelta(days=2, hours=-1),
            capacity=10,
            status="scheduled"
        )
        pkg_type = PackageType.objects.create(
            tenant=self.tenant,
            location=location,
            name="Standard 5-Pack",
            credit_count=5,
            price="100.00",
            validity_days=30
        )
        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            package_type=pkg_type,
            credits_remaining=4,
            expires_at=now + timedelta(days=30)
        )
        booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session,
            credit_source=pkg,
            status="booked"
        )
        
        # Initially client is active
        self.assertTrue(self.client_user.is_active)
        
        # Hitting deactive endpoint as Gym Owner
        url = reverse('users-deactivate', kwargs={'pk': self.client_user.id})
        response = self.api_client.post(url, HTTP_HOST='padel.testserver')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_active'])
        self.assertEqual(response.data['cancelled_bookings_count'], 1)
        
        # Verify user is deactivated
        self.client_user.refresh_from_db()
        self.assertFalse(self.client_user.is_active)
        
        # Verify booking is cancelled & package is refunded
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'cancelled')
        pkg.refresh_from_db()
        self.assertEqual(pkg.credits_remaining, 5) # 4 + 1 refunded
        
        # Hitting deactivate toggle AGAIN to reactivate
        response_reactivate = self.api_client.post(url, HTTP_HOST='padel.testserver')
        self.assertEqual(response_reactivate.status_code, status.HTTP_200_OK)
        self.assertTrue(response_reactivate.data['is_active'])
        self.assertEqual(response_reactivate.data['cancelled_bookings_count'], 0)
        
        self.client_user.refresh_from_db()
        self.assertTrue(self.client_user.is_active)


class ClientDetailedNutritionAPITest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Padel Gym", subdomain="padel")
        self.owner = User.objects.create_user(
            email="owner@padel.com", password="password123", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.client_user = User.objects.create_user(
            email="client@padel.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.owner)

    def test_clients_detailed_nutrition(self):
        from apps.nutritionX.models import NutritionGoal, DailyNutritionProgress, MealLogs, FoodEntry, WaterIntake
        from django.utils import timezone
        
        now = timezone.now().date()
        
        # Create Goal & Progress
        goal = NutritionGoal.objects.create(
            tenant=self.tenant, user=self.client_user,
            calories_goal_kcal="2000", protein_goal_g="150", carbs_goal_g="200", fat_goal_g="70", is_active=True
        )
        DailyNutritionProgress.objects.create(
            tenant=self.tenant, user=self.client_user, goal=goal, date=now,
            water_consumed_ml=1000, calories_consumed_kcal=1800, protein_consumed_g=140, carbs_consumed_g=190, fat_consumed_g=65
        )
        
        # Create meal and food log
        meal = MealLogs.objects.create(
            tenant=self.tenant, user=self.client_user, meal_type="Breakfast", date=now
        )
        FoodEntry.objects.create(
            tenant=self.tenant, user=self.client_user, food=meal, food_name="Oatmeal", calories="300", protein="10", carbs="50", fat="5"
        )
        
        # Create water intake
        WaterIntake.objects.create(
            tenant=self.tenant, user=self.client_user, date=now, amount_ml=1000
        )
        
        # Hit List endpoint
        url_list = reverse('users-clients-detailed-nutrition')
        response_list = self.api_client.get(url_list, HTTP_HOST='padel.testserver')
        self.assertEqual(response_list.status_code, status.HTTP_200_OK)
        self.assertEqual(response_list.data['count'], 1)
        
        client_data = response_list.data['results'][0]
        self.assertEqual(client_data['email'], self.client_user.email)
        self.assertEqual(client_data['stats']['active_goal']['calories_goal_kcal'], "2000")
        self.assertEqual(client_data['stats']['averages']['average_daily_calories_kcal'], 1800.0)
        self.assertEqual(len(client_data['meal_logs']), 1)
        self.assertEqual(client_data['meal_logs'][0]['foods'][0]['food_name'], "Oatmeal")
        
        # Hit Detail endpoint
        url_detail = reverse('users-detailed-nutrition', kwargs={'pk': self.client_user.id})
        response_detail = self.api_client.get(url_detail, HTTP_HOST='padel.testserver')
        self.assertEqual(response_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(response_detail.data['id'], str(self.client_user.id))
        self.assertEqual(len(response_detail.data['water_intakes']), 1)


class ClientDetailedReflectionAPITest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Padel Gym", subdomain="padel")
        self.owner = User.objects.create_user(
            email="owner@padel.com", password="password123", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.client_user = User.objects.create_user(
            email="client@padel.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )
        self.api_client = APIClient()
        self.api_client.force_authenticate(user=self.owner)

    def test_clients_detailed_reflection(self):
        from apps.reflection_logger.models import DailyReflection, MorningEntry, EveningEntry, FocusOption, MorningFocusSelection, MenstrualCycle, CycleDailyLog, SymptomCategory, SymptomTag
        from django.utils import timezone
        
        now = timezone.now().date()
        
        # Create reflection with morning and evening entry
        ref = DailyReflection.objects.create(
            tenant=self.tenant, user=self.client_user, date=now
        )
        morning = MorningEntry.objects.create(
            tenant=self.tenant, reflection=ref, mood="Happy", sleep_quality=8, affirmation="I am strong", gratitude_1="Family"
        )
        focus = FocusOption.objects.create(
            tenant=self.tenant, user=self.client_user, name="Mindfulness", slug="mindfulness"
        )
        MorningFocusSelection.objects.create(
            tenant=self.tenant, morning_entry=morning, focus=focus, action_plan="Meditate for 10 mins"
        )
        
        EveningEntry.objects.create(
            tenant=self.tenant, reflection=ref, stress_level=3, mood_after="Peaceful", highlight_1="Great padel game", lesson="Patience is key"
        )
        
        # Menstrual cycle setup
        MenstrualCycle.objects.create(
            tenant=self.tenant, user=self.client_user, last_period_start_date=now, cycle_length_days=28, period_duration_days=5
        )
        
        # Cycle daily log setup
        cat = SymptomCategory.objects.create(tenant=self.tenant, name="Physical")
        tag = SymptomTag.objects.create(tenant=self.tenant, category=cat, name="Cramps")
        cycle_log = CycleDailyLog.objects.create(tenant=self.tenant, user=self.client_user, date=now, notes="Feeling tired", flow_intensity=2)
        cycle_log.symptoms.add(tag)
        
        # Hit List endpoint
        url_list = reverse('users-clients-detailed-reflection')
        response_list = self.api_client.get(url_list, HTTP_HOST='padel.testserver')
        self.assertEqual(response_list.status_code, status.HTTP_200_OK)
        self.assertEqual(response_list.data['count'], 1)
        
        client_data = response_list.data['results'][0]
        self.assertEqual(client_data['email'], self.client_user.email)
        self.assertEqual(client_data['stats']['average_sleep_quality'], 8.0)
        self.assertEqual(client_data['stats']['average_stress_level'], 3.0)
        self.assertEqual(client_data['stats']['most_common_mood'], "Happy")
        self.assertEqual(client_data['stats']['active_menstrual_cycle']['cycle_length_days'], 28)
        self.assertEqual(len(client_data['daily_reflections']), 1)
        self.assertEqual(client_data['daily_reflections'][0]['morning']['mood'], "Happy")
        self.assertEqual(client_data['daily_reflections'][0]['morning']['focus_selections'][0]['focus_name'], "Mindfulness")
        self.assertEqual(len(client_data['cycle_logs']), 1)
        self.assertEqual(client_data['cycle_logs'][0]['symptoms'][0], "Cramps")
        
        # Hit Detail endpoint
        url_detail = reverse('users-detailed-reflection', kwargs={'pk': self.client_user.id})
        response_detail = self.api_client.get(url_detail, HTTP_HOST='padel.testserver')
        self.assertEqual(response_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(response_detail.data['id'], str(self.client_user.id))
        self.assertEqual(response_detail.data['stats']['average_sleep_quality'], 8.0)


class ClientNutritionGoalAPITest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Padel Gym", subdomain="padel")
        self.owner = User.objects.create_user(
            email="owner@padel.com", password="password123", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.client_user = User.objects.create_user(
            email="client@padel.com", password="password123", role=UserRole.CLIENT, tenant=self.tenant
        )
        self.api_client = APIClient()

    def test_client_set_get_history_goals(self):
        # 1. Initially, getting active goal should return 404
        self.api_client.force_authenticate(user=self.client_user)
        url = reverse('client-nutrition-goals')
        response = self.api_client.get(url, HTTP_HOST='padel.testserver')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # 2. Set new active goal
        payload = {
            "calories_goal_kcal": 2000,
            "protein_goal_g": 150,
            "carbs_goal_g": 200,
            "fat_goal_g": 70,
            "water_intake_goal_ml": 2500,
            "base_water_intake_goal_ml": 2500
        }
        response = self.api_client.post(url, payload, format='json', HTTP_HOST='padel.testserver')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['calories_goal_kcal'], "2000")
        self.assertEqual(response.data['is_active'], True)

        # 3. Get active goal
        response = self.api_client.get(url, HTTP_HOST='padel.testserver')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['calories_goal_kcal'], "2000")

        # 4. Set another active goal (which should deactivate the first one)
        payload2 = {
            "calories_goal_kcal": 2200,
            "protein_goal_g": 160,
            "carbs_goal_g": 220,
            "fat_goal_g": 75,
            "water_intake_goal_ml": 3000
        }
        response = self.api_client.post(url, payload2, format='json', HTTP_HOST='padel.testserver')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['calories_goal_kcal'], "2200")

        # 5. Get active goal (should be the new one)
        response = self.api_client.get(url, HTTP_HOST='padel.testserver')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['calories_goal_kcal'], "2200")

        # 6. Get history (should return both goals, sorted by creation date)
        response = self.api_client.get(url + "?history=true", HTTP_HOST='padel.testserver')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data[0]['calories_goal_kcal'], "2200")
        self.assertEqual(response.data[0]['is_active'], True)
        self.assertEqual(response.data[1]['calories_goal_kcal'], "2000")
        self.assertEqual(response.data[1]['is_active'], False)

    def test_staff_set_get_client_goals(self):
        # 1. Staff sets goal for client using client_id in POST body
        self.api_client.force_authenticate(user=self.owner)
        url = reverse('client-nutrition-goals')
        payload = {
            "client_id": str(self.client_user.id),
            "calories_goal_kcal": 2500,
            "protein_goal_g": 180,
            "carbs_goal_g": 240,
            "fat_goal_g": 80,
            "water_intake_goal_ml": 3200
        }
        response = self.api_client.post(url, payload, format='json', HTTP_HOST='padel.testserver')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['calories_goal_kcal'], "2500")

        # 2. Staff gets active goal for client using client_id query param
        response = self.api_client.get(url + f"?client_id={self.client_user.id}", HTTP_HOST='padel.testserver')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['calories_goal_kcal'], "2500")


class AuthEmailTemplateBrandingTests(TestCase):
    def test_registration_otp_email_template_default_branding(self):
        from django.template.loader import render_to_string
        rendered = render_to_string("emails/registration_otp.html", {"code": "123456"})
        
        self.assertNotIn("Forward Thinking Fitness", rendered)
        self.assertNotIn("Forward Thinking", rendered)
        self.assertNotIn("REGISTARTION", rendered)
        
        # Default fallback is FitVerx
        self.assertIn("FitVerx", rendered)
        self.assertIn("© FitVerx — All rights reserved", rendered)
        self.assertIn("123456", rendered)
        self.assertIn("5 minutes", rendered)

    def test_registration_otp_email_template_custom_platform_name(self):
        from django.template.loader import render_to_string
        rendered = render_to_string(
            "emails/registration_otp.html",
            {"code": "123456", "platform_name": "Go2Padel"}
        )
        
        self.assertNotIn("Forward Thinking Fitness", rendered)
        self.assertNotIn("Forward Thinking", rendered)
        
        # Custom platform name should be rendered, not FitVerx
        self.assertIn("Go2Padel", rendered)
        self.assertIn("© Go2Padel — All rights reserved", rendered)
        self.assertNotIn("FitVerx", rendered)
        self.assertIn("123456", rendered)

    def test_password_reset_otp_email_template_custom_platform_name(self):
        from django.template.loader import render_to_string
        rendered = render_to_string(
            "emails/password_reset_otp.html",
            {"code": "654321", "platform_name": "Apex Athletics"}
        )
        
        self.assertNotIn("Forward Thinking Fitness", rendered)
        self.assertNotIn("Forward Thinking", rendered)
        
        # Custom platform name should be rendered, not FitVerx
        self.assertIn("Apex Athletics", rendered)
        self.assertIn("© Apex Athletics — All rights reserved", rendered)
        self.assertNotIn("FitVerx", rendered)
        self.assertIn("654321", rendered)

    def test_send_email_otp_service_with_custom_platform_name(self):
        from django.core import mail
        from apps.users.services import AuthService, OTPPurpose
        
        # Test Registration OTP with custom platform name
        AuthService.send_email_otp(
            "testuser@example.com", "112233", OTPPurpose.REGISTRATION, platform_name="Go2Padel"
        )
        self.assertEqual(len(mail.outbox), 1)
        reg_email = mail.outbox[0]
        self.assertIn("Go2Padel", reg_email.subject)
        self.assertIn("Go2Padel", reg_email.body)
        self.assertNotIn("FitVerx", reg_email.subject)
        self.assertNotIn("Forward Thinking Fitness", reg_email.body)
        
        # Test Password Reset OTP with custom platform name
        AuthService.send_email_otp(
            "testuser@example.com", "445566", OTPPurpose.PASSWORD_RESET, platform_name="Apex Athletics"
        )
        self.assertEqual(len(mail.outbox), 2)
        reset_email = mail.outbox[1]
        self.assertIn("Apex Athletics", reset_email.subject)
        self.assertIn("Apex Athletics", reset_email.body)
        self.assertNotIn("FitVerx", reset_email.subject)
        self.assertNotIn("Forward Thinking Fitness", reset_email.body)


class TenantLoginIsolationAPITests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant Alpha", subdomain="alpha")
        self.tenant_b = Tenant.objects.create(name="Tenant Beta", subdomain="beta")

        self.user_a = User.objects.create_user(
            email="user_a@alpha.com",
            password="Password123!",
            role=UserRole.CLIENT,
            tenant=self.tenant_a
        )

        self.platform_admin = User.objects.create_user(
            email="superadmin@platform.com",
            password="Password123!",
            role=UserRole.PLATFORM_ADMIN,
            tenant=None
        )

        self.client = APIClient()
        self.login_url = reverse('auth_login')

    def test_login_success_with_matching_tenant(self):
        payload = {
            "email": "user_a@alpha.com",
            "password": "Password123!",
            "tenant_id": str(self.tenant_a.id)
        }
        response = self.client.post(self.login_url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)
        self.assertEqual(response.data['user']['email'], "user_a@alpha.com")
        self.assertEqual(response.data['user']['tenant_id'], str(self.tenant_a.id))

    def test_login_blocked_when_credentials_match_different_tenant(self):
        # User belongs to Tenant Alpha, attempts to log into Tenant Beta
        payload = {
            "email": "user_a@alpha.com",
            "password": "Password123!",
            "tenant_id": str(self.tenant_b.id)
        }
        response = self.client.post(self.login_url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("User credentials do not match the specified gym/tenant.", str(response.data))

    def test_login_blocked_without_tenant_id_for_regular_user(self):
        payload = {
            "email": "user_a@alpha.com",
            "password": "Password123!"
        }
        response = self.client.post(self.login_url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("tenant_id is required to login.", str(response.data))

    def test_login_blocked_with_nonexistent_tenant_id(self):
        import uuid
        payload = {
            "email": "user_a@alpha.com",
            "password": "Password123!",
            "tenant_id": str(uuid.uuid4())
        }
        response = self.client.post(self.login_url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid Tenant ID or tenant not found.", str(response.data))

    def test_login_platform_admin_allowed_without_tenant_id(self):
        payload = {
            "email": "superadmin@platform.com",
            "password": "Password123!"
        }
        response = self.client.post(self.login_url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertTrue(response.data['user']['is_platform_admin'])

    def test_login_with_wrong_password(self):
        payload = {
            "email": "user_a@alpha.com",
            "password": "WrongPassword!",
            "tenant_id": str(self.tenant_a.id)
        }
        response = self.client.post(self.login_url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class StaffRegistrationRequestAPITest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Fit Gym", subdomain="fitgym")
        self.owner = User.objects.create_user(
            email="owner@fitgym.com",
            password="OwnerPassword123!",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )
        self.client_user = User.objects.create_user(
            email="member@fitgym.com",
            password="MemberPassword123!",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        self.client = APIClient()

    def test_staff_registration_submission_and_status(self):
        url = "/api/v1/auth/staff-register/"
        payload = {
            "tenant_id": str(self.tenant.id),
            "email": "trainer_applicant@fitgym.com",
            "password": "TrainerPassword123!",
            "role": "trainer",
            "first_name": "Alex",
            "last_name": "Smith",
            "phone_number": "+1234567890",
            "bio": "Certified strength coach",
            "specialization": "Strength & Conditioning",
            "experience_years": 5,
            "certifications": "CSCS, NASM-CPT"
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["request"]["status"], "pending")
        self.assertEqual(response.data["request"]["email"], "trainer_applicant@fitgym.com")
        request_id = response.data["request"]["id"]

        status_url = "/api/v1/auth/staff-register/status/?email=trainer_applicant@fitgym.com"
        status_res = self.client.get(status_url)
        self.assertEqual(status_res.status_code, status.HTTP_200_OK)
        self.assertEqual(status_res.data["status"], "pending")

        dup_res = self.client.post(url, payload, format="json")
        self.assertEqual(dup_res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_list_and_approve_staff_request(self):
        register_url = "/api/v1/auth/staff-register/"
        payload = {
            "tenant_id": str(self.tenant.id),
            "email": "manager_applicant@fitgym.com",
            "password": "ManagerPassword123!",
            "role": "gym_manager",
            "first_name": "Sarah",
            "last_name": "Connor"
        }
        res = self.client.post(register_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        request_id = res.data["request"]["id"]

        admin_client = APIClient()
        admin_client.force_authenticate(user=self.owner)
        list_url = "/api/v1/staff-requests/"
        list_res = admin_client.get(list_url, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(list_res.status_code, status.HTTP_200_OK)
        results = list_res.data if isinstance(list_res.data, list) else list_res.data.get("results", [])
        self.assertTrue(any(item["id"] == request_id for item in results))

        approve_url = f"/api/v1/staff-requests/{request_id}/approve/"
        approve_res = admin_client.post(approve_url, {}, format="json", HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(approve_res.status_code, status.HTTP_200_OK)
        self.assertEqual(approve_res.data["request"]["status"], "approved")

        new_user = User.objects.get(email="manager_applicant@fitgym.com")
        self.assertEqual(new_user.role, UserRole.GYM_MANAGER)
        self.assertEqual(new_user.tenant, self.tenant)
        self.assertTrue(new_user.is_active)
        self.assertEqual(new_user.profile.first_name, "Sarah")

        login_url = reverse("auth_login")
        login_res = self.client.post(login_url, {
            "email": "manager_applicant@fitgym.com",
            "password": "ManagerPassword123!",
            "tenant_id": str(self.tenant.id)
        }, format="json")
        self.assertEqual(login_res.status_code, status.HTTP_200_OK)
        self.assertIn("access", login_res.data)

    def test_admin_reject_staff_request(self):
        register_url = "/api/v1/auth/staff-register/"
        payload = {
            "tenant_id": str(self.tenant.id),
            "email": "reject_me@fitgym.com",
            "password": "Password123!",
            "role": "trainer",
            "first_name": "Bob"
        }
        res = self.client.post(register_url, payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        request_id = res.data["request"]["id"]

        admin_client = APIClient()
        admin_client.force_authenticate(user=self.owner)
        reject_url = f"/api/v1/staff-requests/{request_id}/reject/"
        reject_res = admin_client.post(reject_url, {"rejection_reason": "Missing required certification"}, format="json", HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(reject_res.status_code, status.HTTP_200_OK)
        self.assertEqual(reject_res.data["request"]["status"], "rejected")
        self.assertEqual(reject_res.data["request"]["rejection_reason"], "Missing required certification")

        self.assertFalse(User.objects.filter(email="reject_me@fitgym.com").exists())


class ClientStaffDeactivationAndLockdownAPITest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Apex Fitness", subdomain="apex")
        self.owner = User.objects.create_user(
            email="owner@apex.com", password="Password123!", role=UserRole.GYM_OWNER, tenant=self.tenant
        )
        self.manager = User.objects.create_user(
            email="manager@apex.com", password="Password123!", role=UserRole.GYM_MANAGER, tenant=self.tenant
        )
        self.trainer = User.objects.create_user(
            email="trainer@apex.com", password="Password123!", role=UserRole.TRAINER, tenant=self.tenant
        )
        self.client_user = User.objects.create_user(
            email="client@apex.com", password="Password123!", role=UserRole.CLIENT, tenant=self.tenant
        )
        self.other_client = User.objects.create_user(
            email="other_client@apex.com", password="Password123!", role=UserRole.CLIENT, tenant=self.tenant
        )
        self.api_client = APIClient()

    def test_client_self_deactivation_without_id(self):
        self.api_client.force_authenticate(user=self.client_user)
        url = reverse('users-self-deactivate')
        response = self.api_client.post(url, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_active'])
        self.client_user.refresh_from_db()
        self.assertFalse(self.client_user.is_active)

    def test_staff_self_deactivation_without_id(self):
        self.api_client.force_authenticate(user=self.trainer)
        url = reverse('users-self-deactivate')
        response = self.api_client.post(url, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_active'])
        self.trainer.refresh_from_db()
        self.assertFalse(self.trainer.is_active)

    def test_client_self_deactivation_me_url(self):
        self.api_client.force_authenticate(user=self.client_user)
        url = reverse('users-me-deactivate')
        response = self.api_client.post(url, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_active'])
        self.client_user.refresh_from_db()
        self.assertFalse(self.client_user.is_active)

    def test_client_cannot_deactivate_other_user(self):
        self.api_client.force_authenticate(user=self.client_user)
        # Attempt via URL pk
        url = reverse('users-deactivate', kwargs={'pk': self.other_client.id})
        response = self.api_client.post(url, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.other_client.refresh_from_db()
        self.assertTrue(self.other_client.is_active)

        # Attempt via request body
        self_url = reverse('users-self-deactivate')
        response_body = self.api_client.post(self_url, {'user_id': str(self.other_client.id)}, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(response_body.status_code, status.HTTP_403_FORBIDDEN)
        self.other_client.refresh_from_db()
        self.assertTrue(self.other_client.is_active)

    def test_staff_cannot_deactivate_other_user(self):
        self.api_client.force_authenticate(user=self.trainer)
        url = reverse('users-deactivate', kwargs={'pk': self.client_user.id})
        response = self.api_client.post(url, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.client_user.refresh_from_db()
        self.assertTrue(self.client_user.is_active)

    def test_client_and_staff_cannot_reactivate_themselves(self):
        # Deactivate client first
        self.client_user.is_active = False
        self.client_user.save()

        self.api_client.force_authenticate(user=self.client_user)
        # Attempt to reactivate via self deactivate
        url = reverse('users-self-deactivate')
        res1 = self.api_client.post(url, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res1.status_code, status.HTTP_403_FORBIDDEN)

        # Attempt to call activate endpoint
        act_url = reverse('users-activate-list')
        res2 = self.api_client.post(act_url, {'user_id': str(self.client_user.id)}, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res2.status_code, status.HTTP_403_FORBIDDEN)

        detail_act_url = reverse('users-activate', kwargs={'pk': self.client_user.id})
        res3 = self.api_client.post(detail_act_url, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(res3.status_code, status.HTTP_403_FORBIDDEN)

        self.client_user.refresh_from_db()
        self.assertFalse(self.client_user.is_active)

    def test_full_deactivation_lockdown_and_admin_reactivation(self):
        # 1. Login through normal auth flow to get real JWT access and refresh tokens
        login_url = reverse('auth_login')
        login_res = self.client.post(login_url, {
            'email': 'client@apex.com',
            'password': 'Password123!',
            'tenant_id': str(self.tenant.id)
        }, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(login_res.status_code, status.HTTP_200_OK)
        access_token = login_res.data['access']
        refresh_token = login_res.data['refresh']

        # Verify access token works
        me_url = reverse('users-me')
        me_res = self.client.get(me_url, HTTP_AUTHORIZATION=f'Bearer {access_token}', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(me_res.status_code, status.HTTP_200_OK)

        # 2. Client self-deactivates using their Bearer token
        deact_url = reverse('users-self-deactivate')
        deact_res = self.client.post(deact_url, HTTP_AUTHORIZATION=f'Bearer {access_token}', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(deact_res.status_code, status.HTTP_200_OK)
        self.assertFalse(deact_res.data['is_active'])

        self.client_user.refresh_from_db()
        self.assertFalse(self.client_user.is_active)

        # 3. Access with existing access token MUST fail (401)
        me_res_after = self.client.get(me_url, HTTP_AUTHORIZATION=f'Bearer {access_token}', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(me_res_after.status_code, status.HTTP_401_UNAUTHORIZED)

        # 4. Token refresh MUST fail (401)
        refresh_url = reverse('token_refresh')
        refresh_res = self.client.post(refresh_url, {'refresh': refresh_token}, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(refresh_res.status_code, status.HTTP_401_UNAUTHORIZED)

        # 5. Logging in again MUST fail (401)
        relogin_res = self.client.post(login_url, {
            'email': 'client@apex.com',
            'password': 'Password123!',
            'tenant_id': str(self.tenant.id)
        }, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(relogin_res.status_code, status.HTTP_401_UNAUTHORIZED)

        # 6. Gym Manager reactivates the client account
        self.api_client.force_authenticate(user=self.manager)
        act_url = reverse('users-activate', kwargs={'pk': self.client_user.id})
        act_res = self.api_client.post(act_url, HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(act_res.status_code, status.HTTP_200_OK)
        self.assertTrue(act_res.data['is_active'])

        self.client_user.refresh_from_db()
        self.assertTrue(self.client_user.is_active)

        # 7. Now client can log in again and access the platform
        relogin_success = self.client.post(login_url, {
            'email': 'client@apex.com',
            'password': 'Password123!',
            'tenant_id': str(self.tenant.id)
        }, format='json', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(relogin_success.status_code, status.HTTP_200_OK)
        new_access = relogin_success.data['access']

        me_res_restored = self.client.get(me_url, HTTP_AUTHORIZATION=f'Bearer {new_access}', HTTP_X_TENANT_ID=str(self.tenant.id))
        self.assertEqual(me_res_restored.status_code, status.HTTP_200_OK)
        self.assertEqual(me_res_restored.data['email'], 'client@apex.com')



