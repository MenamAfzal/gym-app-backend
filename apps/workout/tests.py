from datetime import datetime, timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.users.models import User, UserRole, UserProfile
from apps.scheduling.models import Location, Room, ClassTemplate, ClassSession, Booking, Appointment
from apps.workout.models import Workout, Exercise, WorkoutExercise, WorkoutAssignment
from apps.workout.views import (
    TodayWorkoutAPIView, WorkoutAPIView, clean_session_name, strip_noise_words,
    resolve_movement_level, calculate_workout_session_score,
    WorkoutAssignmentListCreateAPIView, AssignWorkoutAPIView, WorkoutAssignmentDetailAPIView
)


class WorkoutMatchingTestCase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Gym", subdomain="test-gym")
        set_current_tenant(self.tenant)

        self.client_user = User.objects.create_user(
            email="client@testgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        self.client_profile, _ = UserProfile.objects.get_or_create(
            user=self.client_user,
            defaults={"level": "RX1"}
        )
        self.client_profile.level = "RX1"
        self.client_profile.save()

        self.trainer = User.objects.create_user(
            email="trainer@testgym.com",
            password="password123",
            role=UserRole.TRAINER,
            tenant=self.tenant
        )
        UserProfile.objects.create(
            user=self.trainer,
            first_name="Trainer",
            last_name="Joe"
        )

        self.location = Location.objects.create(
            tenant=self.tenant,
            name="Main Location",
            address="123 Fitness St"
        )
        self.room = Room.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Studio 1"
        )

        self.today = timezone.localdate() if hasattr(timezone, "localdate") else timezone.now().date()
        self.today_start = timezone.now().replace(hour=9, minute=0, second=0, microsecond=0)
        self.today_end = self.today_start + timedelta(hours=1)

        self.factory = APIRequestFactory()

    def test_clean_session_name_helper(self):
        self.assertEqual(clean_session_name("Solo - 09:00 AM"), "Solo")
        self.assertEqual(clean_session_name("Solo (9:00 AM)"), "Solo")
        self.assertEqual(clean_session_name("Solo 9:00 AM - 10:00 AM"), "Solo")
        self.assertEqual(clean_session_name("09:00 AM - Solo"), "Solo")
        self.assertEqual(clean_session_name("Solo: 09:00"), "Solo")
        self.assertEqual(clean_session_name("Solo @ 9am"), "Solo")
        self.assertEqual(clean_session_name("Solo Session"), "Solo Session")
        self.assertEqual(strip_noise_words("Solo Session"), "solo")

    def test_resolve_movement_level(self):
        self.assertEqual(resolve_movement_level("RX1"), "Stability")
        self.assertEqual(resolve_movement_level("RX2"), "Strength")
        self.assertEqual(resolve_movement_level("RX3"), "Power")
        self.assertEqual(resolve_movement_level("rx1"), "Stability")
        self.assertEqual(resolve_movement_level("Stability"), "Stability")
        self.assertEqual(resolve_movement_level("power"), "Power")

    def test_today_workout_matching_session_with_scheduled_time(self):
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo - 09:00 AM",
            duration_min=60
        )
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            capacity=10
        )
        booking = Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session,
            status="booked",
            music_preference="Rock"
        )
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Solo Stability Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )
        exercise = Exercise.objects.create(
            tenant=self.tenant,
            name="Plank"
        )
        WorkoutExercise.objects.create(
            tenant=self.tenant,
            workout=workout,
            exercise=exercise,
            order=1
        )

        request = self.factory.get("/api/v1/workout/today/")
        force_authenticate(request, user=self.client_user)
        view = TodayWorkoutAPIView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]), 1)
        workout_data = response.data["data"][0]
        self.assertEqual(workout_data["id"], workout.id)
        self.assertEqual(workout_data["session_id"], session.id)
        self.assertEqual(workout_data["session_name"], "Solo - 09:00 AM")
        self.assertEqual(workout_data["Music Preference"], "Rock")

    def test_today_workout_matching_by_template_category(self):
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Morning Energy - 09:00 AM",
            category="Solo",
            duration_min=60
        )
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            capacity=10
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session,
            status="booked"
        )
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Morning Solo Program",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )
        exercise = Exercise.objects.create(tenant=self.tenant, name="Squat")
        WorkoutExercise.objects.create(tenant=self.tenant, workout=workout, exercise=exercise, order=1)

        request = self.factory.get("/api/v1/workout/today/")
        force_authenticate(request, user=self.client_user)
        view = TodayWorkoutAPIView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["data"][0]["id"], workout.id)

    def test_today_workout_null_start_date_matching(self):
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo - 09:00 AM",
            duration_min=60
        )
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            capacity=10
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session,
            status="booked"
        )
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Open Ended Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer,
            start_date=None,
            end_date=None
        )

        request = self.factory.get("/api/v1/workout/today/")
        force_authenticate(request, user=self.client_user)
        view = TodayWorkoutAPIView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["data"][0]["id"], workout.id)

    def test_today_workout_appointment_solo_matching(self):
        appointment = Appointment.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            provider=self.trainer,
            location=self.location,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            status="scheduled"
        )
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Private Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )

        request = self.factory.get("/api/v1/workout/today/")
        force_authenticate(request, user=self.client_user)
        view = TodayWorkoutAPIView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]), 1)
        self.assertEqual(response.data["data"][0]["id"], workout.id)
        self.assertEqual(response.data["data"][0]["session_id"], appointment.id)

    def test_today_workout_checked_in_status_matched(self):
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo - 09:00 AM",
            duration_min=60
        )
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            capacity=10
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session,
            status="checked_in"
        )
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Checked In Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )

        request = self.factory.get("/api/v1/workout/today/")
        force_authenticate(request, user=self.client_user)
        view = TodayWorkoutAPIView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]), 1)

    def test_workout_list_with_session_id(self):
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo - 09:00 AM",
            duration_min=60
        )
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            capacity=10
        )
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )
        other_workout = Workout.objects.create(
            tenant=self.tenant,
            name="HIIT Workout",
            session_type="HIIT",
            movement_level="Stability",
            created_by=self.trainer
        )

        request = self.factory.get(f"/api/v1/workout/workouts/?session_id={session.id}")
        force_authenticate(request, user=self.client_user)
        view = WorkoutAPIView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, 200)
        data = response.data["data"]
        returned_ids = [w["id"] for w in data]
        self.assertIn(workout.id, returned_ids)
        self.assertNotIn(other_workout.id, returned_ids)

    def test_workout_assignment_overrides_session_workout_for_specific_user_only(self):
        client_b = User.objects.create_user(
            email="clientb@testgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        UserProfile.objects.create(user=client_b, level="RX1", first_name="Client", last_name="B")

        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo - 09:00 AM",
            duration_min=60
        )
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            capacity=10
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session,
            status="booked"
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=client_b,
            session=session,
            status="booked"
        )

        default_workout = Workout.objects.create(
            tenant=self.tenant,
            name="Standard Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )
        custom_workout = Workout.objects.create(
            tenant=self.tenant,
            name="Custom Solo Rehab Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )

        WorkoutAssignment.objects.create(
            tenant=self.tenant,
            workout=custom_workout,
            user=self.client_user,
            session=session,
            assigned_by=self.trainer
        )

        req_a = self.factory.get("/api/v1/workout/today/")
        force_authenticate(req_a, user=self.client_user)
        resp_a = TodayWorkoutAPIView.as_view()(req_a)

        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(len(resp_a.data["data"]), 1)
        self.assertEqual(resp_a.data["data"][0]["id"], custom_workout.id)
        self.assertEqual(resp_a.data["data"][0]["session_id"], session.id)

        req_b = self.factory.get("/api/v1/workout/today/")
        force_authenticate(req_b, user=client_b)
        resp_b = TodayWorkoutAPIView.as_view()(req_b)

        self.assertEqual(resp_b.status_code, 200)
        self.assertEqual(len(resp_b.data["data"]), 1)
        self.assertEqual(resp_b.data["data"][0]["id"], default_workout.id)
        self.assertEqual(resp_b.data["data"][0]["session_id"], session.id)

    def test_workout_direct_assigned_user_overrides_session_workout(self):
        client_b = User.objects.create_user(
            email="clientb2@testgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        UserProfile.objects.create(user=client_b, level="RX1", first_name="Client", last_name="B")

        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo - 09:00 AM",
            duration_min=60
        )
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            capacity=10
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session,
            status="booked"
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=client_b,
            session=session,
            status="booked"
        )

        default_workout = Workout.objects.create(
            tenant=self.tenant,
            name="Standard Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )
        user_specific_workout = Workout.objects.create(
            tenant=self.tenant,
            name="Alice Special Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer,
            assigned_user=self.client_user
        )

        req_a = self.factory.get("/api/v1/workout/today/")
        force_authenticate(req_a, user=self.client_user)
        resp_a = TodayWorkoutAPIView.as_view()(req_a)

        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(resp_a.data["data"][0]["id"], user_specific_workout.id)

        req_b = self.factory.get("/api/v1/workout/today/")
        force_authenticate(req_b, user=client_b)
        resp_b = TodayWorkoutAPIView.as_view()(req_b)

        self.assertEqual(resp_b.status_code, 200)
        self.assertEqual(resp_b.data["data"][0]["id"], default_workout.id)

    def test_assign_workout_api_endpoint_flow(self):
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Assigned Via API Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )

        assign_req = self.factory.post("/api/v1/workout/assign/", {
            "workout_id": workout.id,
            "user_id": str(self.client_user.id),
            "notes": "Assigned by trainer"
        }, format="json")
        force_authenticate(assign_req, user=self.trainer)
        assign_resp = AssignWorkoutAPIView.as_view()(assign_req)

        self.assertEqual(assign_resp.status_code, 201)
        assignment_id = assign_resp.data["data"]["id"]
        self.assertTrue(WorkoutAssignment.objects.filter(id=assignment_id).exists())

        list_req = self.factory.get(f"/api/v1/workout/assignments/?user_id={self.client_user.id}")
        force_authenticate(list_req, user=self.trainer)
        list_resp = WorkoutAssignmentListCreateAPIView.as_view()(list_req)

        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(len(list_resp.data["data"]), 1)

        del_req = self.factory.delete(f"/api/v1/workout/assignments/{assignment_id}/")
        force_authenticate(del_req, user=self.trainer)
        del_resp = WorkoutAssignmentDetailAPIView.as_view()(del_req, pk=assignment_id)

        self.assertEqual(del_resp.status_code, 204)
        self.assertFalse(WorkoutAssignment.objects.filter(id=assignment_id).exists())

    def test_assigned_workout_excluded_from_other_users_workout_list(self):
        client_b = User.objects.create_user(
            email="clientb3@testgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        UserProfile.objects.create(user=client_b, level="RX1", first_name="Client", last_name="B")

        assigned_workout = Workout.objects.create(
            tenant=self.tenant,
            name="Alice Exclusive Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer,
            assigned_user=self.client_user
        )
        public_workout = Workout.objects.create(
            tenant=self.tenant,
            name="Public Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )

        req_b = self.factory.get("/api/v1/workout/workouts/")
        force_authenticate(req_b, user=client_b)
        resp_b = WorkoutAPIView.as_view()(req_b)

        self.assertEqual(resp_b.status_code, 200)
        returned_ids_b = [w["id"] for w in resp_b.data["data"]]
        self.assertIn(public_workout.id, returned_ids_b)
        self.assertNotIn(assigned_workout.id, returned_ids_b)

        req_a = self.factory.get("/api/v1/workout/workouts/")
        force_authenticate(req_a, user=self.client_user)
        resp_a = WorkoutAPIView.as_view()(req_a)

        self.assertEqual(resp_a.status_code, 200)
        returned_ids_a = [w["id"] for w in resp_a.data["data"]]
        self.assertIn(assigned_workout.id, returned_ids_a)
        self.assertIn(public_workout.id, returned_ids_a)
