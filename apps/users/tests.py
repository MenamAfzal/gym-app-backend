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


