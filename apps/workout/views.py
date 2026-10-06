from io import BytesIO
import os
import re
import shutil
import threading
try:
    import pytz
except ImportError:
    from zoneinfo import ZoneInfo
    class PytzFallback:
        @staticmethod
        def timezone(name):
            return ZoneInfo(name)
    pytz = PytzFallback()
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics, permissions
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.generics import get_object_or_404
from config import settings
from django.contrib.auth import get_user_model
User = get_user_model()
try:
    from apps.scheduling.models import ClassSession as Session, Booking
except ImportError:
    Session = None
    Booking = None
try:
    from apps.scheduling.permissions import IsGymStaffOrOwner as IsStaffUser
except ImportError:
    from rest_framework.permissions import IsAdminUser as IsStaffUser
class IsSpecificZoomUpdater(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated
class IsSelfBookingOwner(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated
try:
    from apps.users.models import UserRole
except ImportError:
    UserRole = None

class IsClientUser(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if UserRole and hasattr(request.user, "role"):
            return request.user.role == UserRole.CLIENT or not request.user.is_staff
        return True

class MyzoneHandler:
    def get_user_moves(self, account_id, start_date, end_date):
        return []
from .local_storage import delete_local_file_threaded, save_file_locally
from .models import LikedExercise, Product, MusicPlaylist, Song, WorkoutExercise, WorkoutGroup
from .serializers import BaseWorkoutCopySerializer, DeckOfCardsWorkoutUpdateSerializer, DeckWorkoutMinimalSerializer, DetailedExerciseAlternativeSerializer, ExerciseSubstitutionLogDetailSerializer, ExerciseSubstitutionLogSerializer, \
    LikedExerciseSerializer, MultiLevelDeckWorkoutCreateSerializer, MultiLevelWorkoutCreateSerializer, \
    ProductSerializer, WorkoutUpdateSerializer, Saveserializer, ExerciseUpdateSerializer, \
    ExerciseSaveSerializer
from rest_framework.parsers import MultiPartParser, FormParser
from django.utils import timezone
from django.utils.timezone import now
from .models import Workout, FavoriteWorkout, WeightEntry, WorkoutLog, WorkoutAssignment
from .serializers import (
    WorkoutSerializer, FavoriteWorkoutSerializer,
    WeightEntrySerializer, WorkoutLogSerializer,
    WorkoutCreateWithExercisesSerializer,
    StaffClientWorkoutLogSerializer,
    FirestoreImportSerializer,
    ExerciseSerializer, TagSerializer, EquipmentSerializer, MovesRequestSerializer,
    WorkoutAssignmentSerializer, WorkoutAssignCreateSerializer
)
from .serializers import MusicPlaylistSerializer, SongSerializer
from .models import Equipment, WorkoutTag
from .models import MOVEMENT_PATTERNS, EQUIPMENT, Exercise
import json
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.db import transaction
from datetime import datetime, timedelta
from django.db.models import Count, Q, F
from .models import ExerciseSubstitutionLog
import logging
logger = logging.getLogger(__name__)
RX_LEVEL_MAPPING = {
    "RX1": "Stability",
    "RX2": "Strength",
    "RX3": "Power",
    "Stability": "Stability",
    "Strength": "Strength",
    "Power": "Power",
}

def clean_session_name(name):
    if not name:
        return ''
    s = str(name)
    s = re.sub(r'\b\d{1,2}(?::\d{2})?(?::\d{2})?\s*(?:am|pm|AM|PM)?\s*[-–—to/]+\s*\d{1,2}(?::\d{2})?(?::\d{2})?\s*(?:am|pm|AM|PM)?\b', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\b\d{1,2}(?::\d{2})?(?::\d{2})?\s*(?:am|pm|AM|PM)\b', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\b', ' ', s)
    s = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', ' ', s)
    s = re.sub(r'\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b', ' ', s)
    s = re.sub(r'\b(?:mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'[\(\)\[\]\{\}]', ' ', s)
    s = re.sub(r'[\s\-–—:|@~,.]+', ' ', s)
    return s.strip()

def strip_noise_words(s):
    words = [w for w in s.lower().split() if w not in {'session', 'class', 'workout', 'training'}]
    return ' '.join(words) if words else s.lower()

def resolve_movement_level(level_val):
    if not level_val:
        return None
    lvl_str = str(level_val).strip()
    mapping = {
        "rx1": "Stability",
        "rx2": "Strength",
        "rx3": "Power",
        "stability": "Stability",
        "strength": "Strength",
        "power": "Power",
    }
    return mapping.get(lvl_str.lower(), lvl_str)

def get_session_match_candidates(session_or_booking):
    cands = []
    if session_or_booking is None:
        return cands

    session = getattr(session_or_booking, 'session', session_or_booking)

    if hasattr(session, 'template') and session.template:
        if getattr(session.template, 'name', None):
            cands.append(session.template.name)
        if getattr(session.template, 'category', None):
            cands.append(session.template.category)

    if hasattr(session, 'recurrence_rule') and session.recurrence_rule and getattr(session.recurrence_rule, 'template', None):
        if getattr(session.recurrence_rule.template, 'name', None):
            cands.append(session.recurrence_rule.template.name)
        if getattr(session.recurrence_rule.template, 'category', None):
            cands.append(session.recurrence_rule.template.category)

    if hasattr(session, 'name') and session.name:
        cands.append(session.name)

    if hasattr(session, 'room') and session.room and getattr(session.room, 'name', None):
        cands.append(session.room.name)

    credit_src = getattr(session_or_booking, 'credit_source', None) or getattr(session, 'credit_source', None)
    if credit_src and hasattr(credit_src, 'package_type') and credit_src.package_type:
        cands.append(credit_src.package_type.name)

    return cands

def calculate_workout_session_score(workout, session_candidates):
    w_clean = clean_session_name(workout.session_type or '').lower()
    w_clean_core = strip_noise_words(w_clean)
    w_base_clean = clean_session_name(workout.base_workout_name or '').lower()
    w_name_clean = clean_session_name(workout.name or '').lower()

    best_score = 0
    for cand in session_candidates:
        if not cand:
            continue
        c_raw = str(cand).strip().lower()
        c_clean = clean_session_name(cand).lower()
        c_clean_core = strip_noise_words(c_clean)

        if not c_clean and not c_raw:
            continue

        if w_clean and (w_clean == c_clean or w_clean == c_raw):
            score = 100
        elif w_clean_core and c_clean_core and w_clean_core == c_clean_core:
            score = 90
        elif w_clean_core and c_clean_core and (w_clean_core in c_clean or c_clean_core in w_clean):
            score = 80
        elif (w_clean and c_raw and w_clean in c_raw) or (c_clean and w_clean and c_clean in w_clean):
            score = 70
        elif c_clean_core and ((w_base_clean and c_clean_core in w_base_clean) or (w_name_clean and c_clean_core in w_name_clean)):
            score = 50
        else:
            score = 0

        if score > best_score:
            best_score = score

    if best_score > 0 and workout.workout_exercises.exists():
        best_score += 5

    return best_score

class ProductListCreateAPIView(APIView):
    """
    GET → List products (with filters)
    POST → Create a new product
    """
    permission_classes = [IsAuthenticated]
    def get_queryset(self, request):
        """Build queryset with filters applied."""
        queryset = Product.objects.all().order_by("id")
        barcode = request.query_params.get('barcode')
        if barcode:
            queryset = queryset.filter(barcode=barcode)
        barcode_icontains = request.query_params.get('barcode_icontains')
        if barcode_icontains:
            queryset = queryset.filter(barcode__icontains=barcode_icontains)
        name = request.query_params.get('name')
        if name:
            queryset = queryset.filter(name__icontains=name)
        price = request.query_params.get('price')
        if price:
            queryset = queryset.filter(price=price)
        return queryset
    def get(self, request):
        queryset = self.get_queryset(request)
        serializer = ProductSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
class WorkoutAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        user = request.user
        is_gym_staff = user.is_staff or user.is_superuser or getattr(user, 'role', None) in ['gym_owner', 'gym_manager', 'trainer', 'front_desk']
        session_type = request.query_params.get('session_type') or request.query_params.get('session_name') or request.query_params.get('class_type')
        session_id = request.query_params.get('session_id') or request.query_params.get('class_id')
        date_param = request.query_params.get('date')

        if is_gym_staff:
            workouts = Workout.objects.all()
            assigned_user_param = request.query_params.get('assigned_user') or request.query_params.get('client_id')
            if assigned_user_param:
                workouts = workouts.filter(
                    Q(assigned_user_id=assigned_user_param) |
                    Q(assignments__user_id=assigned_user_param)
                ).distinct()
        else:
            user_rx_level = getattr(user.profile, "level", None)
            movement_level = resolve_movement_level(user_rx_level)
            assigned_workout_ids = set(
                WorkoutAssignment.objects.filter(user=user).values_list('workout_id', flat=True)
            )
            assigned_to_other_workout_ids = set(
                WorkoutAssignment.objects.exclude(user=user).values_list('workout_id', flat=True)
            )
            disallowed_workout_ids = assigned_to_other_workout_ids - assigned_workout_ids
            base_qs = Workout.objects.filter(
                Q(assigned_user__isnull=True) | Q(assigned_user=user) | Q(id__in=assigned_workout_ids)
            ).exclude(id__in=disallowed_workout_ids)
            if movement_level:
                workouts = base_qs.filter(
                    Q(movement_level__iexact=movement_level) | Q(assigned_user=user) | Q(id__in=assigned_workout_ids)
                )
            else:
                workouts = base_qs.filter(
                    Q(assigned_user=user) | Q(id__in=assigned_workout_ids)
                )

        if date_param:
            try:
                target_date = datetime.strptime(date_param, "%Y-%m-%d").date()
                workouts = workouts.filter(
                    Q(start_date__isnull=True) | Q(start_date__lte=target_date)
                ).filter(
                    Q(end_date__isnull=True) | Q(end_date__gte=target_date)
                )
            except ValueError:
                pass

        if session_id:
            session_obj = None
            if Session:
                session_obj = Session.objects.filter(id=session_id).select_related('template', 'recurrence_rule', 'recurrence_rule__template', 'room').first()
            if session_obj:
                cands = get_session_match_candidates(session_obj)
                matched_ids = [w.id for w in workouts if calculate_workout_session_score(w, cands) > 0]
                workouts = workouts.filter(id__in=matched_ids)
        elif session_type:
            cands = [session_type]
            matched_ids = [w.id for w in workouts if calculate_workout_session_score(w, cands) > 0]
            workouts = workouts.filter(id__in=matched_ids)

        serializer = WorkoutSerializer(workouts, many=True, context={"request": request})
        response_data = serializer.data
        favorited_ids = set(
            FavoriteWorkout.objects.filter(
                user=user, is_favorited=True
            ).values_list("workout_id", flat=True)
        )
        for workout in response_data:
            workout["is_favorited"] = workout["id"] in favorited_ids
        return Response(
            {"detail": "Workouts fetched successfully", "data": response_data},
            status=status.HTTP_200_OK,
        )
class CreateWorkoutAPIView(APIView):
    permission_classes = [IsStaffUser]
    def post(self, request):
        serializer = WorkoutCreateWithExercisesSerializer(data=request.data)
        if serializer.is_valid():
            workout = serializer.save(created_by=request.user)
            return Response(WorkoutSerializer(workout, context={"request": request}).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class WorkoutDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request, pk):
        workout = get_object_or_404(Workout, pk=pk)
        serializer = WorkoutSerializer(workout, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        workout = get_object_or_404(Workout, pk=pk)
        is_admin_staff = request.user.is_staff or request.user.is_superuser or getattr(request.user, 'role', None) in ['gym_owner', 'gym_manager']
        if not is_admin_staff and workout.created_by != request.user:
            return Response({"detail": "You are not authorized to edit this workout."}, status=status.HTTP_403_FORBIDDEN)
        serializer = WorkoutUpdateSerializer(workout, data=request.data, partial=False, context={"request": request})
        if serializer.is_valid():
            updated = serializer.save()
            return Response(WorkoutSerializer(updated, context={"request": request}).data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def patch(self, request, pk):
        workout = get_object_or_404(Workout, pk=pk)
        is_admin_staff = request.user.is_staff or request.user.is_superuser or getattr(request.user, 'role', None) in ['gym_owner', 'gym_manager']
        if not is_admin_staff and workout.created_by != request.user:
            return Response({"detail": "You are not authorized to edit this workout."}, status=status.HTTP_403_FORBIDDEN)
        serializer = WorkoutUpdateSerializer(workout, data=request.data, partial=True, context={"request": request})
        if serializer.is_valid():
            updated = serializer.save()
            return Response(WorkoutSerializer(updated, context={"request": request}).data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        workout = get_object_or_404(Workout, pk=pk)
        is_admin_staff = request.user.is_staff or request.user.is_superuser or getattr(request.user, 'role', None) in ['gym_owner', 'gym_manager']
        if not is_admin_staff and workout.created_by != request.user:
            return Response({"detail": "You are not authorized to delete this workout."}, status=status.HTTP_403_FORBIDDEN)
        workout.delete()
        return Response({"detail": "Workout deleted successfully."}, status=status.HTTP_200_OK)


class WorkoutCopyAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def post(self, request, pk=None):
        workout_id = pk or request.data.get("workout_id") or request.data.get("id")
        if not workout_id:
            return Response({"detail": "Workout ID is required."}, status=status.HTTP_400_BAD_REQUEST)

        workout = get_object_or_404(Workout, pk=workout_id)

        custom_name = request.data.get("name") or request.data.get("new_name")
        timestamp_suffix = timezone.now().strftime("%Y%m%d%H%M%S")
        if custom_name:
            new_name = custom_name
        else:
            base_cand = f"{workout.name} (Copy)"
            new_name = f"{workout.name} - copy {timestamp_suffix}" if Workout.objects.filter(name=base_cand).exists() else base_cand

        override_start = request.data.get("start_date")
        override_end = request.data.get("end_date")
        override_session_type = request.data.get("session_type")
        override_movement_level = request.data.get("movement_level")

        target_user = None
        assigned_user_val = request.data.get("assigned_user") or request.data.get("user_id") or request.data.get("client_id")
        if assigned_user_val:
            try:
                target_user = User.objects.get(id=assigned_user_val)
            except (User.DoesNotExist, ValueError):
                return Response({"detail": "Assigned user not found."}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            cloned = Workout.objects.create(
                tenant=getattr(workout, 'tenant', None),
                name=new_name,
                base_workout_name=workout.base_workout_name or workout.name,
                description=workout.description,
                movement_level=override_movement_level or workout.movement_level,
                session_type=override_session_type or workout.session_type,
                workout_type=workout.workout_type,
                video_url=workout.video_url,
                myzone_effort_range=workout.myzone_effort_range,
                notes=workout.notes,
                created_by=request.user,
                assigned_user=target_user or workout.assigned_user,
                start_date=override_start if override_start is not None else workout.start_date,
                end_date=override_end if override_end is not None else workout.end_date,
                session=workout.session,
                is_custom=True if (target_user or workout.assigned_user) else workout.is_custom,
                deck_config=workout.deck_config if workout.workout_type == 4 else None,
            )
            cloned.tags.set(workout.tags.all())
            cloned.equipment.set(workout.equipment.all())

            group_map = {}
            for group in workout.groups.all():
                new_group = WorkoutGroup.objects.create(
                    tenant=getattr(group, 'tenant', None),
                    workout=cloned,
                    group_type=group.group_type,
                    group_number=group.group_number,
                    group_work_minutes=group.group_work_minutes,
                    group_work_seconds=group.group_work_seconds,
                    group_rest_minutes=group.group_rest_minutes,
                    group_rest_seconds=group.group_rest_seconds,
                )
                group_map[group.id] = new_group

            for we in workout.workout_exercises.all().order_by("order", "id"):
                WorkoutExercise.objects.create(
                    tenant=getattr(we, 'tenant', None),
                    workout=cloned,
                    exercise=we.exercise,
                    order=we.order,
                    sets=we.sets,
                    reps=we.reps,
                    rounds=we.rounds,
                    work_seconds=we.work_seconds,
                    work_minutes=we.work_minutes,
                    rest_minutes=we.rest_minutes,
                    rest_seconds=we.rest_seconds,
                    seconds=we.seconds,
                    is_hold=we.is_hold,
                    suit=we.suit,
                    is_joker=we.is_joker,
                    group=group_map.get(we.group_id),
                    video_url=we.video_url,
                    custom_cues=we.custom_cues,
                )

            if cloned.assigned_user:
                WorkoutAssignment.objects.create(
                    tenant=getattr(cloned, 'tenant', None),
                    workout=cloned,
                    user=cloned.assigned_user,
                    session=cloned.session,
                    assigned_by=request.user,
                    session_type=cloned.session_type,
                    date=cloned.start_date or cloned.end_date
                )

        serializer = WorkoutSerializer(cloned, context={"request": request})
        return Response(
            {"detail": "Workout copied successfully.", "data": serializer.data},
            status=status.HTTP_201_CREATED
        )


def is_workout_active_on_date(workout, target_date):
    """
    Checks if a workout is applicable for target_date based on its start_date and end_date.
    Ensures workouts are not returned for dates before the start date or after the end date.
    """
    if not workout:
        return False
    if workout.start_date and target_date < workout.start_date:
        return False
    if workout.end_date and target_date > workout.end_date:
        return False
    return True


def is_assignment_active_on_date(assignment, target_date):
    """
    Checks if a WorkoutAssignment is applicable for the given target_date.
    - An assigned workout is returned only when the requested date falls within its configured date range.
    - If assignment has an explicit date, target_date must match (or fall within multi-day workout range).
    """
    if not assignment or not assignment.workout:
        return False
    w = assignment.workout
    if not is_workout_active_on_date(w, target_date):
        return False
    if assignment.date:
        if w.start_date and w.end_date and w.start_date < w.end_date:
            if not (w.start_date <= target_date <= w.end_date):
                return False
        elif assignment.date != target_date:
            return False
    return True


class TodayWorkoutAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        user = request.user
        user_rx_level = getattr(user.profile, "level", None)
        date_param = request.query_params.get("date")
        if date_param:
            try:
                target_date = datetime.strptime(date_param, "%Y-%m-%d").date()
            except ValueError:
                return Response(
                    {"detail": "Invalid date format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            target_date = timezone.localdate() if hasattr(timezone, "localdate") else now().date()
        movement_level = resolve_movement_level(user_rx_level)
        if not movement_level:
            return Response(
                {"detail": "Invalid movement level for user profile"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        bookings = []
        if Booking:
            bookings = list(
                Booking.objects.filter(
                    client=user,
                    session__start_at__date=target_date,
                )
                .exclude(status__in=["cancelled", "no_show", "waitlisted"])
                .select_related(
                    "session",
                    "session__template",
                    "session__recurrence_rule",
                    "session__recurrence_rule__template",
                    "session__room",
                    "credit_source",
                    "credit_source__package_type",
                )
                .order_by("session__start_at", "created_at")
            )

        user_assignments_qs = (
            WorkoutAssignment.objects.filter(user=user)
            .filter(
                Q(workout__start_date__isnull=True) | Q(workout__start_date__lte=target_date)
            )
            .filter(
                Q(workout__end_date__isnull=True) | Q(workout__end_date__gte=target_date)
            )
            .filter(
                Q(date__isnull=True) |
                Q(date=target_date) |
                Q(workout__start_date__lte=target_date, workout__end_date__gte=target_date)
            )
            .select_related("workout")
            .prefetch_related("workout__workout_exercises")
            .order_by("-created_at")
        )
        user_assignments = [
            a for a in user_assignments_qs
            if is_assignment_active_on_date(a, target_date)
        ]

        user_direct_assigned_workouts = list(
            Workout.objects.filter(assigned_user=user)
            .filter(Q(start_date__isnull=True) | Q(start_date__lte=target_date))
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=target_date))
            .prefetch_related("workout_exercises")
            .order_by("-created_at")
        )
        user_direct_assigned_workouts = [
            w for w in user_direct_assigned_workouts
            if is_workout_active_on_date(w, target_date)
        ]

        favorited_ids = set(
            FavoriteWorkout.objects.filter(
                user=user, is_favorited=True
            ).values_list("workout_id", flat=True)
        )

        if not bookings:
            assigned_today = []
            for a in user_assignments:
                if a.workout and is_assignment_active_on_date(a, target_date):
                    if a.workout not in assigned_today:
                        assigned_today.append(a.workout)
            for w in user_direct_assigned_workouts:
                if is_workout_active_on_date(w, target_date) and w not in assigned_today:
                    assigned_today.append(w)

            if assigned_today:
                workouts_data = []
                for w in assigned_today:
                    serializer = WorkoutSerializer(w, many=False, context={"request": request})
                    w_data = serializer.data
                    w_data["session_id"] = None
                    w_data["session_name"] = w.session_type or w.name
                    w_data["session_start_time"] = None
                    w_data["session_end_time"] = None
                    w_data["Music Preference"] = ""
                    w_data["is_favorited"] = w.id in favorited_ids
                    workouts_data.append(w_data)
                return Response(
                    {"detail": "Today's workouts fetched successfully", "data": workouts_data},
                    status=status.HTTP_200_OK,
                )

            return Response(
                {"detail": "No session booked today"},
                status=status.HTTP_404_NOT_FOUND,
            )

        items_to_match = []
        for b in bookings:
            if b.session:
                sess_name = b.session.name or (b.session.template.name if getattr(b.session, 'template', None) else "")
                items_to_match.append({
                    "session_id": b.session.id,
                    "session_name": sess_name,
                    "session_start_time": getattr(b.session, "start_time", getattr(b.session, "start_at", None)),
                    "session_end_time": getattr(b.session, "end_time", getattr(b.session, "end_at", None)),
                    "music_preference": getattr(b, "music_preference", "") or "",
                    "match_source": b,
                })

        items_to_match.sort(
            key=lambda item: (
                item.get("session_start_time").isoformat()
                if hasattr(item.get("session_start_time"), "isoformat")
                else str(item.get("session_start_time") or ""),
                str(item.get("session_name") or ""),
                str(item.get("session_id") or "")
            )
        )

        booked_session_ids = [it["session_id"] for it in items_to_match if it.get("session_id")]

        other_user_assigned_ids = set(
            WorkoutAssignment.objects.exclude(user=user).values_list("workout_id", flat=True)
        )
        custom_or_assigned_ids = set(
            Workout.objects.filter(
                Q(is_custom=True) |
                Q(assigned_user__isnull=False)
            ).exclude(assigned_user=user).values_list("id", flat=True)
        ) | (other_user_assigned_ids & set(Workout.objects.filter(is_custom=True).values_list("id", flat=True)))

        available_workouts = list(
            Workout.objects.filter(movement_level__iexact=movement_level)
            .exclude(id__in=custom_or_assigned_ids)
            .filter(Q(start_date__isnull=True) | Q(start_date__lte=target_date))
            .filter(Q(end_date__isnull=True) | Q(end_date__gte=target_date))
            .prefetch_related("workout_exercises")
            .order_by("-created_at")
        )
        available_workouts = [
            w for w in available_workouts
            if is_workout_active_on_date(w, target_date)
        ]

        workouts_data = []
        matched_pair_keys = set()
        for item in items_to_match:
            cands = get_session_match_candidates(item["match_source"])

            assigned_candidates = []
            for a in user_assignments:
                if not a.workout or not is_assignment_active_on_date(a, target_date):
                    continue
                score = 0
                if a.session_id and str(a.session_id) == str(item["session_id"]):
                    score = 1000
                elif not a.session_id and a.session_type and any(clean_session_name(a.session_type).lower() == clean_session_name(c).lower() for c in cands if c):
                    score = 900
                elif not a.session_id:
                    w_score = calculate_workout_session_score(a.workout, cands)
                    if w_score > 0:
                        score = 800 + w_score
                    elif a.date == target_date:
                        score = 700
                elif a.session_id and str(a.session_id) != str(item["session_id"]):
                    w_score = calculate_workout_session_score(a.workout, cands)
                    if w_score > 0 and not available_workouts:
                        score = 400 + w_score
                if score > 0:
                    assigned_candidates.append((score, a.workout))

            for w in user_direct_assigned_workouts:
                if not is_workout_active_on_date(w, target_date):
                    continue
                if w.session_id and str(w.session_id) == str(item["session_id"]):
                    score = 1000
                elif not w.session_id:
                    w_score = calculate_workout_session_score(w, cands)
                    score = 850 + w_score if w_score > 0 else 750
                elif not available_workouts:
                    w_score = calculate_workout_session_score(w, cands)
                    score = 450 + w_score if w_score > 0 else 0
                else:
                    score = 0
                if score > 0:
                    assigned_candidates.append((score, w))

            assigned_candidates.sort(key=lambda x: (x[0], x[1].created_at), reverse=True)

            workout = None
            if assigned_candidates:
                workout = assigned_candidates[0][1]
            else:
                scored_workouts = []
                for w in available_workouts:
                    if w.session_id and str(w.session_id) == str(item["session_id"]):
                        s = 950
                    elif not w.session_id:
                        w_score = calculate_workout_session_score(w, cands)
                        s = 500 + w_score if w_score > 0 else 0
                    else:
                        s = calculate_workout_session_score(w, cands)
                    if s > 0:
                        scored_workouts.append((s, w))
                scored_workouts.sort(key=lambda x: (x[0], x[1].created_at), reverse=True)
                if scored_workouts:
                    workout = scored_workouts[0][1]

            if workout:
                pair_key = (workout.id, item["session_id"])
                if pair_key in matched_pair_keys:
                    continue
                matched_pair_keys.add(pair_key)
                serializer = WorkoutSerializer(workout, many=False, context={"request": request, "session_id": item["session_id"]})
                workout_data = serializer.data
                workout_data["session_id"] = item["session_id"]
                workout_data["Music Preference"] = item["music_preference"]
                workout_data["session_name"] = item["session_name"]
                workout_data["session_start_time"] = item["session_start_time"]
                workout_data["session_end_time"] = item["session_end_time"]
                workout_data["is_favorited"] = workout.id in favorited_ids
                workouts_data.append(workout_data)

        workouts_data.sort(
            key=lambda w: (
                w.get("session_start_time").isoformat()
                if hasattr(w.get("session_start_time"), "isoformat")
                else str(w.get("session_start_time") or ""),
                str(w.get("session_name") or ""),
                str(w.get("session_id") or "")
            )
        )

        if not workouts_data:
            return Response(
                {"detail": "No workout with exercises found for today's sessions"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(
            {"detail": "Today's workouts fetched successfully", "data": workouts_data},
            status=status.HTTP_200_OK,
        )


class WorkoutAssignmentListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        is_gym_staff = user.is_staff or user.is_superuser or getattr(user, 'role', None) in ['gym_owner', 'gym_manager', 'trainer', 'front_desk']
        if is_gym_staff:
            queryset = WorkoutAssignment.objects.all().select_related('workout', 'user', 'assigned_by', 'session')
            user_id = request.query_params.get('user_id') or request.query_params.get('client_id')
            if user_id:
                queryset = queryset.filter(user_id=user_id)
            workout_id = request.query_params.get('workout_id')
            if workout_id:
                queryset = queryset.filter(workout_id=workout_id)
            session_id = request.query_params.get('session_id')
            if session_id:
                queryset = queryset.filter(session_id=session_id)
            date_param = request.query_params.get('date')
            if date_param:
                try:
                    d = datetime.strptime(date_param, "%Y-%m-%d").date()
                    queryset = queryset.filter(date=d)
                except ValueError:
                    pass
        else:
            queryset = WorkoutAssignment.objects.filter(user=user).select_related('workout', 'user', 'assigned_by', 'session')

        serializer = WorkoutAssignmentSerializer(queryset, many=True, context={'request': request})
        return Response(
            {"detail": "Workout assignments fetched successfully", "data": serializer.data},
            status=status.HTTP_200_OK
        )

    def post(self, request):
        is_gym_staff = request.user.is_staff or request.user.is_superuser or getattr(request.user, 'role', None) in ['gym_owner', 'gym_manager', 'trainer', 'front_desk']
        if not is_gym_staff:
            return Response({"detail": "Only staff members can assign workouts."}, status=status.HTTP_403_FORBIDDEN)

        serializer = WorkoutAssignCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        workout = serializer.validated_data['workout_obj']
        target_user = serializer.validated_data['user_obj']
        session_obj = serializer.validated_data.get('session_obj')
        session_type = serializer.validated_data.get('session_type') or (session_obj.name if session_obj else workout.session_type)
        date_val = serializer.validated_data.get('date') or workout.start_date or workout.end_date
        notes = serializer.validated_data.get('notes', '')

        assignment, created = WorkoutAssignment.objects.update_or_create(
            workout=workout,
            user=target_user,
            session=session_obj,
            date=date_val,
            defaults={
                'assigned_by': request.user,
                'session_type': session_type,
                'notes': notes,
            }
        )
        workout.is_custom = True
        if workout.assigned_user is None:
            workout.assigned_user = target_user
        workout.save(update_fields=['assigned_user', 'is_custom'])

        output_serializer = WorkoutAssignmentSerializer(assignment, context={'request': request})
        return Response(
            {"detail": "Workout assigned successfully", "data": output_serializer.data},
            status=status.HTTP_201_CREATED
        )


class WorkoutAssignmentDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        assignment = get_object_or_404(WorkoutAssignment, pk=pk)
        is_gym_staff = request.user.is_staff or request.user.is_superuser or getattr(request.user, 'role', None) in ['gym_owner', 'gym_manager', 'trainer', 'front_desk']
        if not is_gym_staff and assignment.user != request.user:
            return Response({"detail": "Not authorized to view this assignment."}, status=status.HTTP_403_FORBIDDEN)
        serializer = WorkoutAssignmentSerializer(assignment, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        assignment = get_object_or_404(WorkoutAssignment, pk=pk)
        is_gym_staff = request.user.is_staff or request.user.is_superuser or getattr(request.user, 'role', None) in ['gym_owner', 'gym_manager', 'trainer', 'front_desk']
        if not is_gym_staff:
            return Response({"detail": "Only staff members can delete workout assignments."}, status=status.HTTP_403_FORBIDDEN)
        assignment.delete()
        return Response({"detail": "Workout assignment deleted successfully"}, status=status.HTTP_204_NO_CONTENT)


class AssignWorkoutAPIView(WorkoutAssignmentListCreateAPIView):
    permission_classes = [IsStaffUser]


class AssignSpecificWorkoutAPIView(APIView):
    permission_classes = [IsStaffUser]

    def post(self, request, pk):
        workout = get_object_or_404(Workout, pk=pk)
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)
        data['workout_id'] = workout.id
        serializer = WorkoutAssignCreateSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        target_user = serializer.validated_data['user_obj']
        session_obj = serializer.validated_data.get('session_obj')
        session_type = serializer.validated_data.get('session_type') or (session_obj.name if session_obj else workout.session_type)
        date_val = serializer.validated_data.get('date') or workout.start_date or workout.end_date
        notes = serializer.validated_data.get('notes', '')

        assignment, created = WorkoutAssignment.objects.update_or_create(
            workout=workout,
            user=target_user,
            session=session_obj,
            date=date_val,
            defaults={
                'assigned_by': request.user,
                'session_type': session_type,
                'notes': notes,
            }
        )
        workout.is_custom = True
        if workout.assigned_user is None:
            workout.assigned_user = target_user
        workout.save(update_fields=['assigned_user', 'is_custom'])

        output_serializer = WorkoutAssignmentSerializer(assignment, context={'request': request})
        return Response(
            {"detail": "Workout assigned successfully", "data": output_serializer.data},
            status=status.HTTP_201_CREATED
        )
class LogWeightAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        """Log weight for an exercise, linked to a specific workout log"""
        serializer = WeightEntrySerializer(data=request.data)
        if serializer.is_valid():
            weight_entry = serializer.save(user=request.user)

            # Emit Rewards Event
            try:
                from apps.rewards.events import RewardEvent
                from apps.rewards.services import RewardEngineService
                tenant_id = getattr(weight_entry, 'tenant_id', None) or getattr(request.user, 'tenant_id', None)
                if tenant_id:
                    RewardEngineService.handle_event(RewardEvent.create_weight_logged(
                        tenant_id=tenant_id,
                        user_id=request.user.id,
                        weight_entry_id=weight_entry.id,
                        weight_kg=float(weight_entry.weight)
                    ))
            except Exception:
                pass

            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class LogCompletionAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        serializer = WorkoutLogSerializer(data=request.data)
        if serializer.is_valid():
            workout_log = serializer.save(user=request.user)
            workout_log.is_completed = True
            workout_log.save(update_fields=["is_completed"])

            try:
                from apps.rewards.events import RewardEvent
                from apps.rewards.services import RewardEngineService

                RewardEngineService.handle_event(RewardEvent.create_workout_completed(
                    tenant_id=workout_log.tenant_id,
                    user_id=request.user.id,
                    workout_log_id=workout_log.id,
                    workout_name=getattr(workout_log.workout, 'name', ''),
                    duration_seconds=workout_log.duration_seconds or 0,
                    occurred_at=workout_log.completed_at
                ))
            except Exception:
                pass

            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class FavoriteWorkoutAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        """Get all favorite workouts for user"""
        user_rx_level = getattr(request.user.profile, "level", None)
        movement_level = RX_LEVEL_MAPPING.get(user_rx_level)
        favorites = (
            FavoriteWorkout.objects.filter(
                user=request.user,
                is_favorited=True,
                workout__movement_level=movement_level,
            )
            .select_related("workout")
        )
        serializer = FavoriteWorkoutSerializer(favorites, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    def post(self, request):
        """Mark a workout as favorite"""
        workout_id = request.data.get("workout_id")
        if not workout_id:
            return Response(
                {"detail": " Workout ID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workout = get_object_or_404(Workout, id=workout_id)
        favorite, created = FavoriteWorkout.objects.get_or_create(
            user=request.user,
            workout=workout,
            defaults={"is_favorited": True},
        )
        if not created:
            if favorite.is_favorited:
                return Response(
                    {"detail": " This workout is already in your favorites."},
                    status=status.HTTP_200_OK,
                )
            else:
                favorite.is_favorited = True
                favorite.save()
                return Response(
                    {"detail": " Workout has been added back to favorites."},
                    status=status.HTTP_200_OK,
                )
        serializer = FavoriteWorkoutSerializer(favorite)
        return Response(
            {"detail": " Workout added to favorites.", "data": serializer.data},
            status=status.HTTP_201_CREATED,
        )
    def delete(self, request):
        """Remove a workout from favorites"""
        workout_id = request.data.get("workout_id")
        if not workout_id:
            return Response(
                {"detail": " Workout ID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workout = get_object_or_404(Workout, id=workout_id)
        try:
            favorite = FavoriteWorkout.objects.get(user=request.user, workout=workout)
            if favorite.is_favorited:
                favorite.is_favorited = False
                favorite.save()
                return Response(
                    {"detail": " Workout removed from favorites."},
                    status=status.HTTP_200_OK,
                )
            else:
                return Response(
                    {"detail": " This workout was not marked as favorite."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except FavoriteWorkout.DoesNotExist:
            return Response(
                {"detail": " This workout is not in your favorites."},
                status=status.HTTP_404_NOT_FOUND,
            )
class StaffCreatedWorkoutsAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]
    def get(self, request):
        """List all workouts created by the authenticated staff"""
        workouts = Workout.objects.filter(created_by=request.user)
        serializer = WorkoutSerializer(workouts, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
class StaffClientWorkoutLogListAPIView(APIView):
    permission_classes = [IsStaffUser]
    def get(self, request):
        """Staff can view completed workout logs of clients (optionally filter by client_id)"""
        client_id = request.query_params.get("client_id")
        logs = WorkoutLog.objects.all().select_related("user", "workout", "session")
        if client_id:
            logs = logs.filter(user_id=client_id)
        serializer = StaffClientWorkoutLogSerializer(logs, many=True)
        return Response(serializer.data)
class ClientPlaylistAPIView(APIView):
    permission_classes = [IsAuthenticated, IsClientUser]
    def get(self, request):
        playlists = MusicPlaylist.objects.prefetch_related('songs').all()
        serializer = GetMusicsSerializer(playlists, many=True)
        return Response(serializer.data)
class PlaylistAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]
    def get(self, request):
        playlists = MusicPlaylist.objects.prefetch_related('songs').all()
        serializer = GetMusicsSerializer(playlists, many=True)
        return Response(serializer.data)
    def post(self, request):
        playlist_id = request.data.get('playlist_id')
        songs_data = request.data.get('songs', [])
        if not playlist_id:
            serializer = MusicPlaylistSerializer(data=request.data, context={'request': request})
            if serializer.is_valid():
                serializer.save(created_by=request.user)
                return Response({'message': 'Playlist created', 'playlist': serializer.data}, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        playlist = get_object_or_404(MusicPlaylist, id=playlist_id, created_by=request.user)
        if len(songs_data) == 0:
            return Response({'message': 'Song must be added when you add in playlist'}, status=status.HTTP_400_BAD_REQUEST)
        for song_data in songs_data:
            tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
            Song.objects.create(playlist=playlist, tenant=tenant, **song_data)
        detail = {
            "playlist": playlist.name,
            "songs": songs_data
        }
        return Response({'message': 'Songs added to playlist', 'detail': detail}, status=status.HTTP_200_OK)
    def delete(self, request):
        playlist_id = request.data.get('playlist_id')
        song_id = request.data.get('song_id')
        if not playlist_id:
            return Response({'error': 'playlist_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        playlist = get_object_or_404(MusicPlaylist, id=playlist_id, created_by=request.user)
        if song_id:
            song = get_object_or_404(Song, id=song_id, playlist=playlist)
            song_title = song.title
            song.delete()
            return Response({'message': f'Song "{song_title}" deleted from playlist.'}, status=status.HTTP_204_NO_CONTENT)
        else:
            playlist_name = playlist.name
            playlist.delete()
            return Response({'message': f'Playlist "{playlist_name}" deleted.'}, status=status.HTTP_204_NO_CONTENT)
class FirestoreImportView(APIView):
    permission_classes = [AllowAny]
    """
    Upload Firestore exerciseLibrary.json and import into DB
    """
    def post(self, request, *args, **kwargs):
        serializer = FirestoreImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        file = serializer.validated_data['file']
        data = json.load(file)
        equipment_lookup = {}
        for _, name in EQUIPMENT.items():
            obj, _ = Equipment.objects.get_or_create(name=name)
            equipment_lookup[name] = obj
        movement_lookup = {}
        for _, name in MOVEMENT_PATTERNS.items():
            obj, _ = WorkoutTag.objects.get_or_create(name=name)
            movement_lookup[name] = obj
        if isinstance(data, dict):
            items = data.items()
        elif isinstance(data, list):
            items = [(str(i), doc) for i, doc in enumerate(data)]
        else:
            return Response(
                {"error": "Unsupported JSON format, must be dict or list"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        created, updated, skipped = 0, 0, 0
        skipped_details = []
        updated_details = []
        for doc_id, exercise_data in items:
            name = exercise_data.get("title") or exercise_data.get("titleAllCaps") or f"Exercise {doc_id}"
            description = exercise_data.get("benefits", "")
            video_url = None
            if exercise_data.get("media") and len(exercise_data["media"]) > 0:
                video_url = exercise_data["media"][0]
            coaching_cues = exercise_data.get("trainerCues", "")
            exercise, created_flag = Exercise.objects.get_or_create(
                name=name.strip(),
                defaults={
                    "description": description.strip() if description else "",
                    "video_url": video_url,
                    "coaching_cues": coaching_cues.strip() if coaching_cues else "",
                }
            )
            if not created_flag:
                needs_update = False
                if not exercise.description and description:
                    exercise.description = description.strip()
                    needs_update = True
                if not exercise.video_url and video_url:
                    exercise.video_url = video_url
                    needs_update = True
                if not exercise.coaching_cues and coaching_cues:
                    exercise.coaching_cues = coaching_cues.strip()
                    needs_update = True
                if needs_update:
                    exercise.save()
                    updated += 1
                    updated_details.append({
                        "doc_id": doc_id,
                        "name": name,
                        "reason": "Updated missing fields"
                    })
                else:
                    skipped += 1
                    skipped_details.append({
                        "doc_id": doc_id,
                        "name": name,
                        "reason": "Already exists with full data"
                    })
                continue
            created += 1
            for eq_id in exercise_data.get("equipment", []):
                eq_name = EQUIPMENT.get(eq_id)
                if eq_name:
                    exercise.equipment.add(equipment_lookup[eq_name])
            for mp_id in exercise_data.get("movementPatterns", []):
                mp_name = MOVEMENT_PATTERNS.get(mp_id)
                if mp_name:
                    exercise.tags.add(movement_lookup[mp_name])
            for tag_id in exercise_data.get("tags", []):
                tag_name = f"Tag {tag_id}"
                tag_obj, _ = WorkoutTag.objects.get_or_create(name=tag_name)
                exercise.tags.add(tag_obj)
        return Response(
            {
                "status": "success",
                "exercises_created": created,
                "exercises_updated": updated,
                "exercises_skipped": skipped,
                "updated_details": updated_details,
                "skipped_details": skipped_details,
            },
            status=status.HTTP_201_CREATED,
        )
class LookupDataAPIView(APIView):
    permission_classes = [IsAuthenticated]
    """
    API to provide lists of all exercises, tags, and equipment.
    """
    def get(self, request):
        exercises = Exercise.objects.all()
        tags = WorkoutTag.objects.all()
        equipment = Equipment.objects.all()
        exercise_serializer = ExerciseSerializer(exercises, many=True)
        tag_serializer = TagSerializer(tags, many=True)
        equipment_serializer = EquipmentSerializer(equipment, many=True)
        response_data = {
            "exercises": exercise_serializer.data,
            "tags": tag_serializer.data,
            "equipment": equipment_serializer.data,
        }
        return Response(response_data)    
class UserMovesView(APIView):
    """
    API endpoint to fetch Myzone moves for a specific user and date range.
    Expects a POST payload with: start_date, end_date, and optionally account_id.
    If account_id is provided, it updates the user's database record if it's missing or different.
    """
    permission_classes = [IsAuthenticated] 
    def post(self, request): 
        payload_account_id = request.data.get('account_id')
        account_id = payload_account_id or request.user.myzone_account_id
        start_date = request.data.get('start_date')
        end_date = request.data.get('end_date')
        if not account_id:
            return Response(
                {"error": "Please provide an 'account_id' in the payload. No Myzone account ID is linked to your profile."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not start_date or not end_date:
            return Response(
                {"error": "Please provide 'start_date' and 'end_date' in the payload. Dates should be in YYYY-MM-DD format."},
                status=status.HTTP_400_BAD_REQUEST
            )
        if payload_account_id and request.user.myzone_account_id != payload_account_id:
            logger.info(f"Updating Myzone account ID for user {request.user.email} from '{request.user.myzone_account_id}' to '{payload_account_id}'")
            request.user.myzone_account_id = payload_account_id
            request.user.save(update_fields=['myzone_account_id'])
        try:
            myzone_api = MyzoneHandler()
            moves = myzone_api.get_user_moves(account_id, start_date, end_date)
            return Response({
                "message": "Successfully fetched Myzone moves.",
                "account_id": account_id,
                "total_moves_fetched": len(moves),
                "moves": moves
            }, status=status.HTTP_200_OK)
        except ValueError as ve:
            return Response({"error": str(ve)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error fetching Myzone moves for {account_id}: {str(e)}", exc_info=True)
            return Response(
                {"error": f"An unexpected error occurred while fetching Myzone data: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
class WorkoutEditAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]
    def get(self, request, pk):
        """Get workout details for editing (same as WorkoutDetailAPIView but accessible to workout creator)"""
        workout = get_object_or_404(Workout, pk=pk)
        is_admin_staff = request.user.is_staff or request.user.is_superuser or request.user.role in ['gym_owner', 'gym_manager']
        if not is_admin_staff and workout.created_by != request.user:
            return Response(
                {"detail": "You are not authorized to edit this workout."}, 
                status=status.HTTP_403_FORBIDDEN
            )
        serializer = WorkoutSerializer(workout)
        return Response(serializer.data, status=status.HTTP_200_OK)
    def put(self, request, pk):
        """Update an existing workout with exercises and groups"""
        workout = get_object_or_404(Workout, pk=pk)
        is_admin_staff = request.user.is_staff or request.user.is_superuser or request.user.role in ['gym_owner', 'gym_manager']
        if not is_admin_staff and workout.created_by != request.user:
            return Response(
                {"detail": "You are not authorized to edit this workout."}, 
                status=status.HTTP_403_FORBIDDEN
            )
        serializer = WorkoutUpdateSerializer(workout, data=request.data, partial=False)
        if serializer.is_valid():
            updated_workout = serializer.save()
            return Response(
                WorkoutSerializer(updated_workout).data, 
                status=status.HTTP_200_OK
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    def patch(self, request, pk):
        """Partially update an existing workout"""
        workout = get_object_or_404(Workout, pk=pk)
        is_admin_staff = request.user.is_staff or request.user.is_superuser or request.user.role in ['gym_owner', 'gym_manager']
        if not is_admin_staff and workout.created_by != request.user:
            return Response(
                {"detail": "You are not authorized to edit this workout."}, 
                status=status.HTTP_403_FORBIDDEN
            )
        serializer = WorkoutUpdateSerializer(workout, data=request.data, partial=True)
        if serializer.is_valid():
            updated_workout = serializer.save()
            return Response(
                WorkoutSerializer(updated_workout).data, 
                status=status.HTTP_200_OK
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        workout = get_object_or_404(Workout, pk=pk)
        is_admin_staff = request.user.is_staff or request.user.is_superuser or getattr(request.user, 'role', None) in ['gym_owner', 'gym_manager']
        if not is_admin_staff and workout.created_by != request.user:
            return Response({"detail": "You are not authorized to delete this workout."}, status=status.HTTP_403_FORBIDDEN)
        workout.delete()
        return Response({"detail": "Workout deleted successfully."}, status=status.HTTP_200_OK)
class MultipleCreateWorkoutAPIView(APIView):
    permission_classes = [IsStaffUser]
    def post(self, request):
        """Create workouts for multiple levels or single level (staff only)"""
        serializer = MultiLevelWorkoutCreateSerializer(data=request.data)
        if serializer.is_valid():
            workouts = serializer.save(created_by=request.user)
            response_data = [WorkoutSerializer(workout).data for workout in workouts]
            return Response({
                "message": f"Successfully created {len(workouts)} workout(s)",
                "workouts": response_data
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class StaffCreatedWorkoutsAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]
    def get(self, request):
        """List all workouts created by the authenticated staff and trainer, grouped by base workout"""
        TRAINER_EMAILS = ["evan@ftfstrong.com", "hello@trainftf.com"]
        workouts = Workout.objects.filter(
        Q(created_by=request.user) |
        Q(created_by__email__in=TRAINER_EMAILS)
    )
        date_param = request.query_params.get("date")
        if date_param:
            try:
                filter_date = datetime.strptime(date_param, "%Y-%m-%d").date()
                workouts = workouts.filter(
                    Q(start_date__lte=filter_date) &
                    (Q(end_date__gte=filter_date) | Q(end_date__isnull=True))
                )
            except ValueError:
                return Response({"error": "Invalid date format. Use YYYY-MM-DD."},
                                status=status.HTTP_400_BAD_REQUEST)
        workouts = workouts.order_by("base_workout_name", "movement_level")
        grouped_workouts = {}
        for workout in workouts:
            base_name = workout.base_workout_name or workout.name
            grouped_workouts.setdefault(base_name, []).append(WorkoutSerializer(workout, context={'request': request}).data)
        response_data = [
            {
                "base_workout_name": base_name,
                "workouts": workout_list,
                "total_levels": len(workout_list)
            }
            for base_name, workout_list in grouped_workouts.items()
        ]
        return Response(
            {
                "grouped_workouts": response_data,
                "total_base_workouts": len(grouped_workouts),
            },
            status=status.HTTP_200_OK,
        )
class LikeExerciseAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        exercise_id = request.data.get("exercise_id")
        try:
            exercise = Exercise.objects.get(id=exercise_id)
        except (Workout.DoesNotExist, Exercise.DoesNotExist):
            return Response({"error": "Invalid workout or exercise"}, status=400) 
        liked, created = LikedExercise.objects.get_or_create(
            user=request.user,
            exercise=exercise, 
            defaults={"is_liked": True},
        )
        if not created:
            liked.is_liked = not liked.is_liked  
            liked.save()
        return Response({
            "exercise": exercise.name,
            "is_liked": liked.is_liked
        })
    def get(self, request):
        """Get All Liked Excercise of the user"""
        user_id = request.query_params.get("user_id")
        if not user_id:
            return Response({"error": "user_id is required"}, status=400)
        try:
            user = User.objects.get(id=user_id)
            liked_exercises = LikedExercise.objects.filter(user=user, is_liked=True)
        except User.DoesNotExist:
            return Response({"error": "Invalid user"}, status=400)
        serializer = LikedExerciseSerializer(liked_exercises, many=True)
        return Response(serializer.data)
class DeepCloneBaseWorkoutAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]
    def post(self, request):
        """
        Deep clone all workouts under a base_workout_name.
        Supports both regular and Deck-of-Cards workouts.
        Payload:
        {
            "base_workout_name": "Full Body Strength Training2",
            "new_base_workout_name": "Full Body Strength Training2 Copy",   
            "start_date": "2025-09-20",   
            "end_date": "2025-10-05"      
        }
        """
        serializer = BaseWorkoutCopySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        base_name = data["base_workout_name"]
        new_base_name = data.get("new_base_workout_name") or ""
        override_start = data.get("start_date")
        override_end = data.get("end_date")
        workouts_qs = Workout.objects.filter(base_workout_name=base_name).order_by("id")
        if not workouts_qs.exists():
            return Response(
                {"detail": f"No workouts found for base_workout_name '{base_name}'."},
                status=status.HTTP_404_NOT_FOUND,
            )
        created_workouts = []
        timestamp_suffix = timezone.now().strftime("%Y%m%d%H%M%S")
        try:
            with transaction.atomic():
                for w in workouts_qs:
                    final_base_name = new_base_name if new_base_name else base_name
                    movement_level = w.movement_level or ""
                    new_name = f"{final_base_name} ({movement_level})" if movement_level else final_base_name
                    if Workout.objects.filter(name=new_name).exists():
                        new_name = f"{new_name} - copy {timestamp_suffix}"
                    cloned = Workout.objects.create(
                        name=new_name,
                        base_workout_name=final_base_name,
                        description=w.description,
                        movement_level=w.movement_level,
                        session_type=w.session_type,
                        workout_type=w.workout_type,
                        video_url=w.video_url,
                        myzone_effort_range=w.myzone_effort_range,
                        notes=w.notes,
                        created_by=request.user,
                        start_date=override_start if override_start is not None else w.start_date,
                        end_date=override_end if override_end is not None else w.end_date,
                        deck_config=w.deck_config if w.workout_type == 4 else None,  
                    )
                    cloned.tags.set(w.tags.all())
                    cloned.equipment.set(w.equipment.all())
                    if w.workout_type != 4:
                        for group in w.groups.all():
                            new_group = WorkoutGroup.objects.create(
                                workout=cloned,
                                group_type=group.group_type,
                                group_number=group.group_number,
                                group_work_minutes=group.group_work_minutes,
                                group_work_seconds=group.group_work_seconds,
                                group_rest_minutes=group.group_rest_minutes,
                                group_rest_seconds=group.group_rest_seconds,
                            )
                            for we in group.exercises.all().order_by("order", "id"):
                                WorkoutExercise.objects.create(
                                    workout=cloned,
                                    exercise=we.exercise,
                                    order=we.order,
                                    sets=we.sets,
                                    reps=we.reps,
                                    rounds=we.rounds,
                                    work_seconds=we.work_seconds,
                                    work_minutes=we.work_minutes,
                                    rest_minutes=we.rest_minutes,
                                    rest_seconds=we.rest_seconds,
                                    group=new_group,
                                    video_url=we.video_url,
                                    custom_cues=we.custom_cues,
                                )
                        for we in w.workout_exercises.filter(group__isnull=True).order_by("order", "id"):
                            WorkoutExercise.objects.create(
                                workout=cloned,
                                exercise=we.exercise,
                                order=we.order,
                                sets=we.sets,
                                reps=we.reps,
                                rounds=we.rounds,
                                work_seconds=we.work_seconds,
                                work_minutes=we.work_minutes,
                                rest_minutes=we.rest_minutes,
                                rest_seconds=we.rest_seconds,
                                group=None,
                                video_url=we.video_url,
                                custom_cues=we.custom_cues,
                            )
                    created_workouts.append(cloned)
        except Exception as exc:
            return Response(
                {"detail": "Failed to deep-clone workouts", "error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        serialized = WorkoutSerializer(created_workouts, many=True, context={'request': request}).data
        response = {
            "grouped_workouts": [
                {
                    "base_workout_name": new_base_name if new_base_name else base_name,
                    "workouts": serialized,
                    "total_levels": len(serialized),
                }
            ],
            "total_base_workouts": 1,
        }
        return Response(response, status=status.HTTP_201_CREATED)
class MultiLevelDeckWorkoutAPIView(APIView):
    permission_classes = [IsStaffUser]
    def post(self, request):
        serializer = MultiLevelDeckWorkoutCreateSerializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        workouts = serializer.save(created_by=request.user)
        response_serializer = DeckWorkoutMinimalSerializer(workouts, many=True)
        return Response({
            "message": f"Successfully created {len(workouts)} deck-of-cards workout(s)",
            "workouts": response_serializer.data
        }, status=status.HTTP_201_CREATED)
class DeckOfCardsWorkoutUpdateAPIView(APIView):
    permission_classes = [IsStaffUser]
    def patch(self, request, pk):
        workout = get_object_or_404(Workout, id=pk, workout_type=4)
        serializer = DeckOfCardsWorkoutUpdateSerializer(data=request.data, partial=True)
        if serializer.is_valid():
            updated_workout = serializer.update(workout, serializer.validated_data)
            return Response(
                {
                    "message": "Deck configuration updated successfully",
                    "workout": WorkoutSerializer(updated_workout).data,
                },
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class createExerice(APIView):
    permission_classes = [IsStaffUser]
    def post(self, request):
        serializer = Saveserializer(data=request.data)
        if serializer.is_valid():
            order = serializer.save()
            return Response(Saveserializer(order).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class WorkoutExerciseUpdateCreate(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]
    def _update_exercise(self, request, pk, partial=False):
        exercise = get_object_or_404(Exercise, pk=pk)
        serializer = ExerciseUpdateSerializer(
            exercise,
            data=request.data,
            partial=partial,
            context={'request': request}
        )
        if serializer.is_valid():
            video_file = request.FILES.get("video_file")
            if video_file:
                rel_path, local_url = save_file_locally(video_file, folder="videos")
                serializer.validated_data["video_url"] = local_url
                serializer.validated_data["video_file"] = rel_path
                serializer.validated_data["upload_status"] = "uploaded"
            exercise = serializer.save()
            if 'tags' in request.data:
                exercise.tags.set(serializer.validated_data.get('tags', []))
            if 'equipment' in request.data:
                exercise.equipment.set(serializer.validated_data.get('equipment', []))
            response_serializer = ExerciseUpdateSerializer(exercise)
            response_data = response_serializer.data
            if video_file:
                response_data["message"] = "Video saved locally"
            return Response(response_data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    def _create_exercise(self, request):
        serializer = ExerciseSaveSerializer(data=request.data)
        if serializer.is_valid():
            video_file = request.FILES.get("video_file")
            exercise = serializer.save(video_file=None)
            if video_file:
                rel_path, local_url = save_file_locally(video_file, folder="videos")
                exercise.video_url = local_url
                exercise.video_file = rel_path
                exercise.upload_status = "uploaded"
                exercise.save(update_fields=["video_url", "video_file", "upload_status"])
            response_data = ExerciseSerializer(exercise).data
            if video_file:
                response_data["message"] = "Video saved locally"
            return Response(response_data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    def _remove_exercise(self, request, pk):
        exercise = get_object_or_404(Exercise, pk=pk)
        file_url = exercise.video_url
        exercise.delete()
        if file_url:
            delete_local_file_threaded(file_url)
        return Response(
            {"message": "Exercise and video deleted successfully."},
            status=status.HTTP_204_NO_CONTENT
        )
    def put(self, request, pk):
        return self._update_exercise(request, pk, partial=False)
    def post(self, request):
        return self._create_exercise(request)
    def delete(self, request, pk):
        return self._remove_exercise(request, pk)
class ExerciseFilterAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def get(self, request):
        queryset = Exercise.objects.all()
        filters = Q()
        tag_ids = request.query_params.getlist('tags', [])
        tag_names = request.query_params.getlist('tag_names', [])
        if tag_ids:
            filters &= Q(tags__id__in=tag_ids)
        if tag_names:
            filters &= Q(tags__name__in=tag_names)
        equipment_ids = request.query_params.getlist('equipment', [])
        equipment_names = request.query_params.getlist('equipment_names', [])
        if equipment_ids:
            filters &= Q(equipment__id__in=equipment_ids)
        if equipment_names:
            filters &= Q(equipment__name__in=equipment_names)
        if filters:
            queryset = queryset.filter(filters)
        queryset = queryset.prefetch_related('tags', 'equipment').distinct()
        serializer = ExerciseSerializer(queryset, many=True)
        return Response(serializer.data)
class MusicPlaylistListCreateView(APIView):
    """
    GET: List all playlists for the logged-in user.
    POST: Create a new playlist (Triggers 'Let The Music Play' reward).
    """
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request):
        playlists = MusicPlaylist.objects.filter(created_by=request.user).order_by('-created_at')
        serializer = MusicPlaylistSerializer(playlists, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    def post(self, request):
        serializer = MusicPlaylistSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save(created_by=request.user)
            return Response({
                "message": "Playlist created successfully!",
                "data": serializer.data
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class MusicPlaylistDetailView(APIView):
    """
    GET: View a specific playlist.
    DELETE: Delete the entire playlist (and its songs).
    """
    permission_classes = [permissions.IsAuthenticated]
    def get_object(self, pk, user):
        return get_object_or_404(MusicPlaylist, id=pk, created_by=user)
    def get(self, request, pk):
        playlist = self.get_object(pk, request.user)
        serializer = MusicPlaylistSerializer(playlist)
        return Response(serializer.data, status=status.HTTP_200_OK)
    def delete(self, request, pk):
        playlist = self.get_object(pk, request.user)
        playlist.delete()
        return Response(
            {"message": "Playlist deleted successfully"}, 
            status=status.HTTP_204_NO_CONTENT
        )
class SongDetailView(APIView):
    """
    DELETE: Remove just one song from a playlist.
    """
    permission_classes = [permissions.IsAuthenticated]
    def delete(self, request, pk):
        song = get_object_or_404(Song, id=pk, playlist__created_by=request.user)
        song.delete()
        return Response(
            {"message": "Song deleted successfully"}, 
            status=status.HTTP_204_NO_CONTENT
        )
class BulkExerciseVideoUploadView(APIView):
    """
    API to upload multiple exercise videos to S3.
    Features:
    1. Instant API response (moves files to temp folder).
    2. Background Thread for S3 uploads.
    3. Heavy logging (print statements) for monitoring.
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    def get_canonical_name(self, name):
        """Standardizes names (e.g., '2-Point' -> 'twopoint')."""
        if not name: return ""
        name = name.lower()
        name = name.replace('&', 'and').replace('+', 'and')
        digit_map = {
            '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four',
            '5': 'five', '6': 'six', '7': 'seven', '8': 'eight', '9': 'nine'
        }
        for digit, word in digit_map.items():
            name = name.replace(digit, word)
        return re.sub(r'[^a-z]', '', name)
    def process_bulk_uploads_background(self, tasks):
        success_count = 0
        fail_count = 0
        for i, task in enumerate(tasks):
            file_path = task['file_path']
            exercise_id = task['exercise_id']
            filename = task['filename']
            content_type = task['content_type']
            exercise_name = task['exercise_name']
            try:
                if not os.path.exists(file_path):
                    fail_count += 1
                    continue
                rel_path, video_url = save_file_locally(file_path, folder="videos", filename=filename)
                rows_updated = Exercise.objects.filter(id=exercise_id).update(
                    video_url=video_url,
                    video_file=rel_path,
                    upload_status='uploaded'
                )
                if rows_updated:
                    success_count += 1
            except Exception:
                fail_count += 1
                Exercise.objects.filter(id=exercise_id).update(upload_status='failed')
            finally:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
    def post(self, request):
        files = request.FILES.getlist('videos')
        if not files:
            return Response({"detail": "No video files provided."}, status=status.HTTP_400_BAD_REQUEST)
        all_exercises = Exercise.objects.only('id', 'name')
        exercise_map = {}
        for ex in all_exercises:
            c_name = self.get_canonical_name(ex.name)
            if c_name: exercise_map[c_name] = ex
        matched_tasks = []
        unmatched_files = []
        temp_dir = os.path.join(os.sep, 'tmp', 'ftf_bulk_uploads')
        if not os.path.exists(temp_dir):
            os.makedirs(temp_dir, exist_ok=True)
        for file in files:
            original_filename = file.name
            clean_name = os.path.splitext(original_filename)[0]
            canon_name = self.get_canonical_name(clean_name)
            matched_exercise = exercise_map.get(canon_name)
            if matched_exercise:
                target_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}_{original_filename}")
                try:
                    if hasattr(file, 'temporary_file_path'):
                        shutil.move(file.temporary_file_path(), target_path)
                    else:
                        with open(target_path, 'wb+') as dest:
                            for chunk in file.chunks():
                                dest.write(chunk)
                    matched_tasks.append({
                        'file_path': target_path,
                        'exercise_id': matched_exercise.id,
                        'exercise_name': matched_exercise.name,
                        'filename': original_filename,
                        'content_type': getattr(file, 'content_type', 'application/octet-stream')
                    })
                except Exception:
                    pass
            else:
                unmatched_files.append(original_filename)
        if matched_tasks:
            worker_thread = threading.Thread(
                target=self.process_bulk_uploads_background,
                args=(matched_tasks,),
                daemon=True
            )
            worker_thread.start()
        return Response(
            {
                "message": "Upload started in background.",
                "files_processing": len(matched_tasks),
                "files_unmatched": len(unmatched_files),
                "unmatched_list": unmatched_files
            },
            status=status.HTTP_202_ACCEPTED
        )
class ExerciseAlternativeRecommendationView(APIView):
    """
    COMPLEX API: Returns a ranked list of alternative exercises based on 
    tag intersection cardinality and equipment availability.
    """
    permission_classes = [IsAuthenticated]
    def get(self, request, exercise_id):
        try:
            target_exercise = Exercise.objects.prefetch_related('tags', 'equipment').get(id=exercise_id)
        except Exercise.DoesNotExist:
            return Response({"error": "Target exercise construction failed"}, status=status.HTTP_404_NOT_FOUND)
        target_tags = list(target_exercise.tags.all())
        target_tag_ids = [t.id for t in target_tags]
        candidates = Exercise.objects.exclude(id=exercise_id).filter(
            tags__id__in=target_tag_ids
        ).annotate(
            match_score=Count('tags', filter=Q(tags__id__in=target_tag_ids))
        ).filter(
            match_score__gt=0 
        ).order_by('-match_score', 'name') 
        top_candidates = candidates[:10]
        serializer = DetailedExerciseAlternativeSerializer(top_candidates, many=True)
        return Response({
            "target": target_exercise.name,
            "total_matches": candidates.count(),
            "alternatives": serializer.data
        })
class LogExerciseSubstitutionView(APIView):
    """
    Handles the logging of an exercise swap event. 
    Links the swap to the Workout Template immediately when the user selects it.
    """
    permission_classes = [IsAuthenticated]
    def post(self, request):
        class PreSubmissionValidator:
            def __init__(self, data):
                self.data = data
                self.errors = []
            def verify_integrity(self):
                w_id = self.data.get('workout')
                if not w_id:
                    self.errors.append("Missing 'workout' ID (Template ID).")
                    return False
                if not Workout.objects.filter(id=w_id).exists():
                    self.errors.append(f"Workout Template with ID {w_id} does not exist.")
                    return False
                return True
        validator = PreSubmissionValidator(request.data)
        if not validator.verify_integrity():
            return Response({"validation_errors": validator.errors}, status=status.HTTP_400_BAD_REQUEST)
        serializer = ExerciseSubstitutionLogSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class StaffSubstitutionHistoryView(generics.ListAPIView):
    """
    STAFF API: Returns a paginated list of ALL substitutions across the system.
    Optimized with select_related to prevent N+1 DB query explosion.
    """
    permission_classes = [IsAuthenticated, IsStaffUser]
    serializer_class = ExerciseSubstitutionLogDetailSerializer
    def get_queryset(self):
        return ExerciseSubstitutionLog.objects.select_related(
            'user', 
            'user__profile',  
            'original_exercise', 
            'substituted_exercise', 
            'workout'
        ).order_by('-created_at')
class UserSubstitutionHistoryView(generics.ListAPIView):
    """
    CLIENT API: Returns the history of substitutions for the authenticated user 
    for the CURRENT DAY in Allentown (America/New_York).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ExerciseSubstitutionLogDetailSerializer
    def get_queryset(self):
        user = self.request.user
        target_timezone = pytz.timezone('America/New_York')
        now_in_allentown = timezone.now().astimezone(target_timezone)
        start_of_day_local = now_in_allentown.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day_local = start_of_day_local + timedelta(days=1)
        return ExerciseSubstitutionLog.objects.filter(
            user=user,
            created_at__gte=start_of_day_local,
            created_at__lt=end_of_day_local
        ).select_related(
            'original_exercise', 
            'substituted_exercise', 
            'workout' 
        ).order_by('-created_at')
