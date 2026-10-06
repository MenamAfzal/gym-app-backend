from datetime import datetime, timedelta, timezone as dt_timezone
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.users.models import User, UserRole, UserProfile
from apps.scheduling.models import Location, Room, ClassTemplate, ClassSession, Booking
from apps.workout.models import Workout, Exercise, WorkoutExercise, WorkoutAssignment, WorkoutLog
from apps.workout.views import (
    TodayWorkoutAPIView, WorkoutAPIView, clean_session_name, strip_noise_words,
    resolve_movement_level, calculate_workout_session_score,
    WorkoutAssignmentListCreateAPIView, AssignWorkoutAPIView, WorkoutAssignmentDetailAPIView,
    CreateWorkoutAPIView, LogCompletionAPIView, WorkoutDetailAPIView, WorkoutEditAPIView,
    WorkoutCopyAPIView
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

    def test_today_workout_does_not_match_appointments(self):
        from apps.scheduling.models import Appointment
        Appointment.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            provider=self.trainer,
            location=self.location,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            status="scheduled"
        )
        Workout.objects.create(
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

        self.assertEqual(response.status_code, 404)

    def test_today_workout_sessions_sorted_by_time(self):
        template1 = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Session Early - 08:00 AM",
            duration_min=60
        )
        template2 = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Session Late - 10:00 AM",
            duration_min=60
        )
        session_late = ClassSession.objects.create(
            tenant=self.tenant,
            template=template2,
            room=self.room,
            start_at=self.today_start + timedelta(hours=2),
            end_at=self.today_end + timedelta(hours=2),
            capacity=10
        )
        session_early = ClassSession.objects.create(
            tenant=self.tenant,
            template=template1,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            capacity=10
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session_late,
            status="confirmed"
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session_early,
            status="confirmed"
        )
        Workout.objects.create(
            tenant=self.tenant,
            name="Early Workout",
            session_type="Session Early",
            movement_level="Stability",
            created_by=self.trainer
        )
        Workout.objects.create(
            tenant=self.tenant,
            name="Late Workout",
            session_type="Session Late",
            movement_level="Stability",
            created_by=self.trainer
        )

        request = self.factory.get("/api/v1/workout/today/")
        force_authenticate(request, user=self.client_user)
        view = TodayWorkoutAPIView.as_view()
        response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["data"]), 2)
        self.assertEqual(str(response.data["data"][0]["session_id"]), str(session_early.id))
        self.assertEqual(str(response.data["data"][1]["session_id"]), str(session_late.id))

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

    def test_workout_assignment_reversion_to_default_workout(self):
        client_b = User.objects.create_user(
            email="clientb_revert@testgym.com",
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
            status="confirmed"
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=client_b,
            session=session,
            status="confirmed"
        )

        workout_1 = Workout.objects.create(
            tenant=self.tenant,
            name="Standard Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )
        ex = Exercise.objects.create(tenant=self.tenant, name="Standard Push Up")
        WorkoutExercise.objects.create(tenant=self.tenant, workout=workout_1, exercise=ex, order=1)

        workout_2 = Workout.objects.create(
            tenant=self.tenant,
            name="Custom Solo Workout 2",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )
        ex2 = Exercise.objects.create(tenant=self.tenant, name="Custom Pull Up")
        WorkoutExercise.objects.create(tenant=self.tenant, workout=workout_2, exercise=ex2, order=1)

        assign_req = self.factory.post(
            "/api/v1/workout/assign/",
            {
                "workout_id": workout_2.id,
                "user_id": str(self.client_user.id),
                "session_id": str(session.id)
            },
            format="json"
        )
        force_authenticate(assign_req, user=self.trainer)
        assign_resp = AssignWorkoutAPIView.as_view()(assign_req)
        self.assertEqual(assign_resp.status_code, 201)
        assignment_id = assign_resp.data["data"]["id"]

        today_req_a = self.factory.get("/api/v1/workout/today/")
        force_authenticate(today_req_a, user=self.client_user)
        today_resp_a = TodayWorkoutAPIView.as_view()(today_req_a)
        self.assertEqual(today_resp_a.status_code, 200)
        self.assertEqual(today_resp_a.data["data"][0]["id"], workout_2.id)

        today_req_b = self.factory.get("/api/v1/workout/today/")
        force_authenticate(today_req_b, user=client_b)
        today_resp_b = TodayWorkoutAPIView.as_view()(today_req_b)
        self.assertEqual(today_resp_b.status_code, 200)
        self.assertEqual(today_resp_b.data["data"][0]["id"], workout_1.id)

        del_req = self.factory.delete(f"/api/v1/workout/assignments/{assignment_id}/")
        force_authenticate(del_req, user=self.trainer)
        del_resp = WorkoutAssignmentDetailAPIView.as_view()(del_req, pk=assignment_id)
        self.assertEqual(del_resp.status_code, 204)

        today_req_a_after = self.factory.get("/api/v1/workout/today/")
        force_authenticate(today_req_a_after, user=self.client_user)
        today_resp_a_after = TodayWorkoutAPIView.as_view()(today_req_a_after)
        self.assertEqual(today_resp_a_after.status_code, 200)
        self.assertEqual(today_resp_a_after.data["data"][0]["id"], workout_1.id)

        today_req_b_after = self.factory.get("/api/v1/workout/today/")
        force_authenticate(today_req_b_after, user=client_b)
        today_resp_b_after = TodayWorkoutAPIView.as_view()(today_req_b_after)
        self.assertEqual(today_resp_b_after.status_code, 200)
        self.assertEqual(today_resp_b_after.data["data"][0]["id"], workout_1.id)

    def test_create_workout_with_session_and_user_scoping(self):
        client_b = User.objects.create_user(
            email="clientb_scoping@testgym.com",
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
            status="confirmed"
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=client_b,
            session=session,
            status="confirmed"
        )

        workout_default = Workout.objects.create(
            tenant=self.tenant,
            name="Default Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )
        ex = Exercise.objects.create(tenant=self.tenant, name="Default Ex")
        WorkoutExercise.objects.create(tenant=self.tenant, workout=workout_default, exercise=ex, order=1)

        ex2 = Exercise.objects.create(tenant=self.tenant, name="Ex Two")
        create_req = self.factory.post(
            "/api/v1/workout/create/",
            {
                "name": "Created For Client A",
                "session_type": "Solo",
                "movement_level": "Stability",
                "user_id": str(self.client_user.id),
                "session_id": session.id,
                "workout_type": 1,
                "exercises": [{"exercise_id": ex2.id, "sets": 3, "reps": 10}]
            },
            format="json"
        )
        force_authenticate(create_req, user=self.trainer)
        create_resp = CreateWorkoutAPIView.as_view()(create_req)
        self.assertEqual(create_resp.status_code, 201)
        created_w_id = create_resp.data["id"]

        w_created = Workout.objects.get(id=created_w_id)
        self.assertTrue(w_created.is_custom)
        self.assertEqual(w_created.assigned_user, self.client_user)
        self.assertEqual(w_created.session, session)
        self.assertTrue(WorkoutAssignment.objects.filter(workout=w_created, user=self.client_user).exists())

        today_req_a = self.factory.get("/api/v1/workout/today/")
        force_authenticate(today_req_a, user=self.client_user)
        today_resp_a = TodayWorkoutAPIView.as_view()(today_req_a)
        self.assertEqual(today_resp_a.status_code, 200)
        self.assertEqual(today_resp_a.data["data"][0]["id"], created_w_id)

        today_req_b = self.factory.get("/api/v1/workout/today/")
        force_authenticate(today_req_b, user=client_b)
        today_resp_b = TodayWorkoutAPIView.as_view()(today_req_b)
        self.assertEqual(today_resp_b.status_code, 200)
        self.assertEqual(today_resp_b.data["data"][0]["id"], workout_default.id)

    def test_multi_session_completion_isolation(self):
        template1 = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo - 09:00 AM",
            duration_min=60
        )
        session1 = ClassSession.objects.create(
            tenant=self.tenant,
            template=template1,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            capacity=10
        )
        template2 = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo - 04:00 PM",
            duration_min=60
        )
        session2 = ClassSession.objects.create(
            tenant=self.tenant,
            template=template2,
            room=self.room,
            start_at=self.today_start + timedelta(hours=7),
            end_at=self.today_end + timedelta(hours=7),
            capacity=10
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session1,
            status="confirmed"
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session2,
            status="confirmed"
        )

        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Standard Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )
        ex = Exercise.objects.create(tenant=self.tenant, name="Exercise Solo")
        WorkoutExercise.objects.create(tenant=self.tenant, workout=workout, exercise=ex, order=1)

        log_req = self.factory.post(
            "/api/v1/workout/log-completion/",
            {
                "workout_id": workout.id,
                "session_id": session1.id
            },
            format="json"
        )
        force_authenticate(log_req, user=self.client_user)
        log_resp = LogCompletionAPIView.as_view()(log_req)
        self.assertEqual(log_resp.status_code, 201)

        today_req = self.factory.get("/api/v1/workout/today/")
        force_authenticate(today_req, user=self.client_user)
        today_resp = TodayWorkoutAPIView.as_view()(today_req)
        self.assertEqual(today_resp.status_code, 200)

        data = today_resp.data["data"]
        self.assertEqual(len(data), 2)
        sess1_item = next(item for item in data if item["session_id"] == session1.id)
        sess2_item = next(item for item in data if item["session_id"] == session2.id)

        self.assertTrue(sess1_item["is_completed"])
        self.assertFalse(sess2_item["is_completed"])

    def test_today_workout_multiple_sessions_same_name(self):
        tmpl = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo",
            duration_min=60
        )
        s1 = ClassSession.objects.create(
            tenant=self.tenant,
            template=tmpl,
            room=self.room,
            start_at=self.today_start,
            end_at=self.today_end,
            capacity=10
        )
        s2 = ClassSession.objects.create(
            tenant=self.tenant,
            template=tmpl,
            room=self.room,
            start_at=self.today_start + timedelta(hours=5),
            end_at=self.today_end + timedelta(hours=5),
            capacity=10
        )
        Booking.objects.create(tenant=self.tenant, client=self.client_user, session=s1, status="booked")
        Booking.objects.create(tenant=self.tenant, client=self.client_user, session=s2, status="booked")

        w1 = Workout.objects.create(
            tenant=self.tenant,
            name="Standard Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer,
            session=s1
        )
        ex = Exercise.objects.create(tenant=self.tenant, name="Exercise Multi")
        WorkoutExercise.objects.create(tenant=self.tenant, workout=w1, exercise=ex, order=1)

        req = self.factory.get("/api/v1/workout/today/")
        force_authenticate(req, user=self.client_user)
        res = TodayWorkoutAPIView.as_view()(req)
        self.assertEqual(res.status_code, 200)
        data = res.data["data"]
        self.assertEqual(len(data), 2)
        sess_ids = {d["session_id"] for d in data}
        self.assertIn(s1.id, sess_ids)
        self.assertIn(s2.id, sess_ids)
        self.assertEqual(data[0]["id"], w1.id)
        self.assertEqual(data[1]["id"], w1.id)

        w2 = Workout.objects.create(
            tenant=self.tenant,
            name="Afternoon Solo Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer,
            session=s2
        )
        WorkoutExercise.objects.create(tenant=self.tenant, workout=w2, exercise=ex, order=1)

        res2 = TodayWorkoutAPIView.as_view()(req)
        self.assertEqual(res2.status_code, 200)
        data2 = res2.data["data"]
        self.assertEqual(len(data2), 2)
        item1 = next(d for d in data2 if d["session_id"] == s1.id)
        item2 = next(d for d in data2 if d["session_id"] == s2.id)
        self.assertEqual(item1["id"], w1.id)
        self.assertEqual(item2["id"], w2.id)

    def test_single_workout_edit(self):
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Original Workout",
            session_type="Strength",
            movement_level="Stability",
            created_by=self.trainer
        )
        req = self.factory.patch(
            f"/api/v1/workout/workouts/{workout.id}/",
            {"name": "Updated Workout Name", "notes": "Updated notes"},
            format="json"
        )
        force_authenticate(req, user=self.trainer)
        res = WorkoutDetailAPIView.as_view()(req, pk=workout.id)
        self.assertEqual(res.status_code, 200)
        workout.refresh_from_db()
        self.assertEqual(workout.name, "Updated Workout Name")
        self.assertEqual(workout.notes, "Updated notes")

    def test_single_workout_delete(self):
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Workout To Delete",
            session_type="Strength",
            movement_level="Stability",
            created_by=self.trainer
        )
        req = self.factory.delete(f"/api/v1/workout/workouts/{workout.id}/")
        force_authenticate(req, user=self.trainer)
        res = WorkoutDetailAPIView.as_view()(req, pk=workout.id)
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Workout.objects.filter(id=workout.id).exists())

    def test_single_workout_copy(self):
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Base Single Workout",
            session_type="Solo",
            movement_level="Stability",
            created_by=self.trainer
        )
        ex = Exercise.objects.create(tenant=self.tenant, name="Exercise For Copy")
        WorkoutExercise.objects.create(tenant=self.tenant, workout=workout, exercise=ex, order=1, sets=3, reps=10)

        req = self.factory.post(
            f"/api/v1/workout/workouts/{workout.id}/copy/",
            {"name": "Cloned Single Workout"},
            format="json"
        )
        force_authenticate(req, user=self.trainer)
        res = WorkoutCopyAPIView.as_view()(req, pk=workout.id)
        self.assertEqual(res.status_code, 201)
        cloned_id = res.data["data"]["id"]
        cloned = Workout.objects.get(id=cloned_id)
        self.assertEqual(cloned.name, "Cloned Single Workout")
        self.assertEqual(cloned.workout_exercises.count(), 1)
        self.assertEqual(cloned.workout_exercises.first().exercise.name, "Exercise For Copy")

    def test_workout_creation_sets_date_on_client_assignment_record(self):
        """
        Verify that creating a workout with an assigned client and session sets
        the selected workout date on the background WorkoutAssignment record.
        """
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo Session Template",
            category="Solo",
            duration_min=60
        )
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=1),
            capacity=10
        )
        ex = Exercise.objects.create(tenant=self.tenant, name="Push Up")

        create_req = self.factory.post(
            "/api/v1/workout/create/",
            {
                "name": "Oct 2 Assigned Workout",
                "session_type": "Solo",
                "movement_level": "Stability",
                "user_id": str(self.client_user.id),
                "session_id": session.id,
                "start_date": "2026-10-02",
                "end_date": "2026-10-02",
                "workout_type": 1,
                "exercises": [{"exercise_id": ex.id, "sets": 3, "reps": 12}]
            },
            format="json"
        )
        force_authenticate(create_req, user=self.trainer)
        create_resp = CreateWorkoutAPIView.as_view()(create_req)
        self.assertEqual(create_resp.status_code, 201)
        created_w_id = create_resp.data["id"]

        workout = Workout.objects.get(id=created_w_id)
        self.assertEqual(str(workout.start_date), "2026-10-02")
        self.assertEqual(str(workout.end_date), "2026-10-02")

        assignment = WorkoutAssignment.objects.get(workout=workout, user=self.client_user)
        self.assertIsNotNone(assignment.date)
        self.assertEqual(str(assignment.date), "2026-10-02")
        self.assertEqual(assignment.session, session)

    def test_today_workout_api_date_boundary_filtering(self):
        """
        Ensure Today's Workout API only returns a workout when the requested date falls
        within its configured date range (e.g. October 2nd only), and returns 404 for
        dates outside the range (Sept 28, 29, 30, Oct 1, Oct 3, Oct 4).
        """
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Solo Session Template",
            category="Solo",
            duration_min=60
        )
        session = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=timezone.now(),
            end_at=timezone.now() + timedelta(hours=1),
            capacity=10
        )
        ex = Exercise.objects.create(tenant=self.tenant, name="Deadlift")

        # Create workout configured only for October 2nd, 2026
        create_req = self.factory.post(
            "/api/v1/workout/create/",
            {
                "name": "Strict October 2 Workout",
                "session_type": "Solo",
                "movement_level": "Stability",
                "user_id": str(self.client_user.id),
                "session_id": session.id,
                "start_date": "2026-10-02",
                "end_date": "2026-10-02",
                "workout_type": 1,
                "exercises": [{"exercise_id": ex.id, "sets": 4, "reps": 8}]
            },
            format="json"
        )
        force_authenticate(create_req, user=self.trainer)
        create_resp = CreateWorkoutAPIView.as_view()(create_req)
        self.assertEqual(create_resp.status_code, 201)
        created_w_id = create_resp.data["id"]

        # 1. Matching target date: 2026-10-02 -> MUST be returned
        req_match = self.factory.get("/api/v1/workout/today/?date=2026-10-02")
        force_authenticate(req_match, user=self.client_user)
        resp_match = TodayWorkoutAPIView.as_view()(req_match)
        self.assertEqual(resp_match.status_code, 200)
        self.assertEqual(len(resp_match.data["data"]), 1)
        self.assertEqual(resp_match.data["data"][0]["id"], created_w_id)

        # 2. Outside dates: past dates and future dates -> MUST NOT be returned
        outside_dates = [
            "2026-09-28",
            "2026-09-29",
            "2026-09-30",
            "2026-10-01",
            "2026-10-03",
            "2026-10-04",
        ]
        for d in outside_dates:
            req_outside = self.factory.get(f"/api/v1/workout/today/?date={d}")
            force_authenticate(req_outside, user=self.client_user)
            resp_outside = TodayWorkoutAPIView.as_view()(req_outside)
            self.assertEqual(
                resp_outside.status_code,
                404,
                f"Workout should not be returned for date {d}"
            )

    def test_today_workout_api_date_boundary_filtering_with_booked_session(self):
        """
        Verify that even when the client has a session booked on past or future dates,
        an assigned workout configured strictly for 2026-10-02 is NOT returned for
        bookings on other dates (e.g. 2026-10-01 or 2026-10-03).
        """
        template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Daily Solo Session",
            category="Solo",
            duration_min=60
        )
        start1 = datetime(2026, 10, 1, 9, 0, tzinfo=dt_timezone.utc)
        session_oct1 = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=start1,
            end_at=start1 + timedelta(hours=1),
            capacity=10
        )
        start2 = datetime(2026, 10, 2, 9, 0, tzinfo=dt_timezone.utc)
        session_oct2 = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=start2,
            end_at=start2 + timedelta(hours=1),
            capacity=10
        )
        start3 = datetime(2026, 10, 3, 9, 0, tzinfo=dt_timezone.utc)
        session_oct3 = ClassSession.objects.create(
            tenant=self.tenant,
            template=template,
            room=self.room,
            start_at=start3,
            end_at=start3 + timedelta(hours=1),
            capacity=10
        )

        # Bookings on Oct 1, Oct 2, Oct 3
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session_oct1,
            status="booked"
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session_oct2,
            status="booked"
        )
        Booking.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            session=session_oct3,
            status="booked"
        )

        ex = Exercise.objects.create(tenant=self.tenant, name="Squat")
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Oct 2 Only Workout",
            session_type="Solo",
            movement_level="Stability",
            assigned_user=self.client_user,
            session=session_oct2,
            start_date=datetime(2026, 10, 2).date(),
            end_date=datetime(2026, 10, 2).date(),
            created_by=self.trainer,
            is_custom=True
        )
        WorkoutExercise.objects.create(tenant=self.tenant, workout=workout, exercise=ex, order=1)
        WorkoutAssignment.objects.create(
            tenant=self.tenant,
            workout=workout,
            user=self.client_user,
            session=session_oct2,
            date=datetime(2026, 10, 2).date(),
            session_type="Solo",
            assigned_by=self.trainer
        )

        # Oct 2: Returns the assigned workout
        req_oct2 = self.factory.get("/api/v1/workout/today/?date=2026-10-02")
        force_authenticate(req_oct2, user=self.client_user)
        resp_oct2 = TodayWorkoutAPIView.as_view()(req_oct2)
        self.assertEqual(resp_oct2.status_code, 200)
        self.assertEqual(resp_oct2.data["data"][0]["id"], workout.id)

        # Oct 1: Outside configured date range -> workout must NOT be returned
        req_oct1 = self.factory.get("/api/v1/workout/today/?date=2026-10-01")
        force_authenticate(req_oct1, user=self.client_user)
        resp_oct1 = TodayWorkoutAPIView.as_view()(req_oct1)
        self.assertEqual(resp_oct1.status_code, 404)

        # Oct 3: Outside configured date range -> workout must NOT be returned
        req_oct3 = self.factory.get("/api/v1/workout/today/?date=2026-10-03")
        force_authenticate(req_oct3, user=self.client_user)
        resp_oct3 = TodayWorkoutAPIView.as_view()(req_oct3)
        self.assertEqual(resp_oct3.status_code, 404)

    def test_today_workout_api_multi_day_date_range(self):
        """
        Verify that a workout configured with a multi-day range (e.g. 2026-10-02 to 2026-10-04)
        is returned for dates within the range and rejected outside the range.
        """
        ex = Exercise.objects.create(tenant=self.tenant, name="Pull Up")
        workout = Workout.objects.create(
            tenant=self.tenant,
            name="Oct 2 to Oct 4 Range Workout",
            session_type="Solo",
            movement_level="Stability",
            assigned_user=self.client_user,
            start_date=datetime(2026, 10, 2).date(),
            end_date=datetime(2026, 10, 4).date(),
            created_by=self.trainer,
            is_custom=True
        )
        WorkoutExercise.objects.create(tenant=self.tenant, workout=workout, exercise=ex, order=1)
        WorkoutAssignment.objects.create(
            tenant=self.tenant,
            workout=workout,
            user=self.client_user,
            date=datetime(2026, 10, 2).date(),
            session_type="Solo",
            assigned_by=self.trainer
        )

        # Before range: 2026-10-01 -> 404
        req_before = self.factory.get("/api/v1/workout/today/?date=2026-10-01")
        force_authenticate(req_before, user=self.client_user)
        self.assertEqual(TodayWorkoutAPIView.as_view()(req_before).status_code, 404)

        # Within range: 2026-10-02, 2026-10-03, 2026-10-04 -> 200
        for in_date in ["2026-10-02", "2026-10-03", "2026-10-04"]:
            req_in = self.factory.get(f"/api/v1/workout/today/?date={in_date}")
            force_authenticate(req_in, user=self.client_user)
            resp_in = TodayWorkoutAPIView.as_view()(req_in)
            self.assertEqual(resp_in.status_code, 200, f"Expected 200 for date {in_date}")
            self.assertEqual(resp_in.data["data"][0]["id"], workout.id)

        # After range: 2026-10-05 -> 404
        req_after = self.factory.get("/api/v1/workout/today/?date=2026-10-05")
        force_authenticate(req_after, user=self.client_user)
        self.assertEqual(TodayWorkoutAPIView.as_view()(req_after).status_code, 404)


