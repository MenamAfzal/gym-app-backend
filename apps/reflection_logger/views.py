import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger(__name__)
from django.shortcuts import get_object_or_404
from datetime import datetime, date

from .models import CycleDailyLog, DailyReflection, FocusOption, MenstrualCycle, SymptomCategory
from .serializers import CreateCustomTagSerializer, CycleDailyLogSerializer, DailyReflectionSerializer, FocusOptionSerializer, MenstrualCycleCreateSerializer, SymptomCategorySerializer
from .services import analytics, ai_quotes
from .services.analytics import CycleAnalytics
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.db.models.deletion import ProtectedError
# ============================
# DAILY REFLECTION CRUD
# ============================

class DailyReflectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user_id = request.query_params.get("user")
        start = request.query_params.get("start")
        end = request.query_params.get("end")

        qs = DailyReflection.objects.all().select_related("morning", "evening")

        if user_id:
            qs = qs.filter(user__id=user_id)

        if start and end:
            qs = qs.filter(date__range=(start, end))

        qs = qs.order_by("-date")[:1000]
        serializer = DailyReflectionSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    def post(self, request):
        logger.info(f"DailyReflectionAPIView POST payload: {request.data}")
        try:
            serializer = DailyReflectionSerializer(data=request.data, context={"request": request})
            serializer.is_valid(raise_exception=True)
            reflection = serializer.save()
            out = DailyReflectionSerializer(reflection, context={"request": request})
            return Response(out.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"DailyReflectionAPIView POST failed. Error: {e} | Payload: {request.data}", exc_info=True)
            raise
    
    
    def patch(self, request, pk):
        logger.info(f"DailyReflectionAPIView PATCH payload: {request.data} | pk: {pk}")
        try:
            reflection = get_object_or_404(DailyReflection, pk=pk)
            # partial=True allows omitting 'date' if you just want to update 'morning'
            serializer = DailyReflectionSerializer(reflection, data=request.data, partial=True, context={"request": request})
            serializer.is_valid(raise_exception=True)
            reflection = serializer.save()
            return Response(DailyReflectionSerializer(reflection, context={"request": request}).data)
        except Exception as e:
            logger.error(f"DailyReflectionAPIView PATCH failed. Error: {e} | pk: {pk} | Payload: {request.data}", exc_info=True)
            raise
    
    


class DailyReflectionDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        reflection = get_object_or_404(DailyReflection, pk=pk)
        serializer = DailyReflectionSerializer(reflection, context={"request": request})
        return Response(serializer.data)

    def post(self, request, pk):
        logger.info(f"DailyReflectionDetailAPIView POST payload: {request.data} | pk: {pk}")
        try:
            reflection = get_object_or_404(DailyReflection, pk=pk)
            serializer = DailyReflectionSerializer(reflection, data=request.data, context={"request": request})
            serializer.is_valid(raise_exception=True)
            reflection = serializer.save()
            out = DailyReflectionSerializer(reflection, context={"request": request})
            return Response(out.data)
        except Exception as e:
            logger.error(f"DailyReflectionDetailAPIView POST failed. Error: {e} | pk: {pk} | Payload: {request.data}", exc_info=True)
            raise
    


# ============================
# TODAY'S ENTRY
# ============================

class TodayReflectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        d = date.today()
        reflection, _ = DailyReflection.objects.get_or_create(user=request.user, date=d)
        serializer = DailyReflectionSerializer(reflection, context={"request": request})
        return Response(serializer.data)


# ============================
# STREAKS
# ============================

class ReflectionStreakAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        current, longest = analytics.calculate_streaks(request.user)
        return Response({"current_streak": current, "longest_streak": longest})


# ============================
# ANALYTICS
# ============================

class ReflectionAnalyticsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        start = request.query_params.get("start")
        end = request.query_params.get("end")
        user_id = request.query_params.get("user")   

        if not start or not end:
            return Response({"detail": "Provide start and end in YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

         
        target_user = request.user
        if user_id:
            User = get_user_model()
            target_user = get_object_or_404(User, pk=user_id)

         
        mood = analytics.get_user_mood_trends(target_user, start, end)
        sleep = analytics.get_sleep_vs_mood_correlation(target_user, start, end)
        focus = analytics.get_focus_effort_stats(target_user, start, end)

        return Response({
            "mood_trends": mood,
            "sleep_mood": sleep,
            "focus_stats": focus,
        })


# ============================
# FOCUS OPTIONS
# ============================

class FocusOptionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = FocusOption.objects.filter(is_active=True).filter(
            Q(user=None) | Q(user=request.user)
        )
        serializer = FocusOptionSerializer(qs, many=True)
        return Response(serializer.data)

class FocusOptionDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        focus_option = get_object_or_404(FocusOption, pk=pk, user=request.user)

        try:
            focus_option.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
            
        except ProtectedError:
            return Response(
                {"detail": "Cannot delete this focus because it is used in your past reflections."},
                status=status.HTTP_400_BAD_REQUEST
            )

# ============================
# AI QUOTE
# ============================

class AIQuoteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        date_str = request.query_params.get("date")
        user = request.user
        
        focus_topics = []

        if date_str:
            reflection = DailyReflection.objects.filter(user=user, date=date_str).first()
            
            if reflection:
                if hasattr(reflection, 'morning') and reflection.morning:
                    # Access the nested focus selections
                    selections = reflection.morning.focus_selections.all()
                    for sel in selections:
                        focus_topics.append(sel.focus.name)

                # 2. Check Evening Reflections
                if hasattr(reflection, 'evening') and reflection.evening:
                    # Access the nested focus reflections
                    reflections = reflection.evening.focus_reflections.all()
                    for ref in reflections:
                        focus_topics.append(ref.focus.name)

        # Get quote based on these topics (or random if list is empty)
        q = ai_quotes.get_daily_quote(focus_topics=focus_topics)
        
        return Response({"quote": q})


class MenstrualCycleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = MenstrualCycleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cycle = serializer.save(user=request.user)

        year = date.today().year

        analytics = CycleAnalytics(
            start_date=cycle.last_period_start_date,
            cycle_length=cycle.cycle_length_days,
            period_length=cycle.period_duration_days,
        )

        yearly_cycles = analytics.generate_year(year)

        return Response(
            {
                "year": year,
                "assumption": "Cycle length and duration assumed constant",
                "input": {
                    "last_period_start_date": cycle.last_period_start_date,
                    "cycle_length_days": cycle.cycle_length_days,
                    "period_duration_days": cycle.period_duration_days,
                },
                "cycles": yearly_cycles,
            },
            status=status.HTTP_201_CREATED,
        )
    
    def patch(self, request):
        cycle = (
            MenstrualCycle.objects
            .filter(user=request.user)
            .order_by("-created_at")
            .first()
        )

        if not cycle:
            return Response(
                {"detail": "No cycle data found to update."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = MenstrualCycleCreateSerializer(
            cycle,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        cycle = serializer.save()

        year = date.today().year

        analytics = CycleAnalytics(
            start_date=cycle.last_period_start_date,
            cycle_length=cycle.cycle_length_days,
            period_length=cycle.period_duration_days,
        )

        yearly_cycles = analytics.generate_year(year)

        return Response(
            {
                "year": year,
                "assumption": "Cycle length and duration assumed constant",
                "input": {
                    "last_period_start_date": cycle.last_period_start_date,
                    "cycle_length_days": cycle.cycle_length_days,
                    "period_duration_days": cycle.period_duration_days,
                },
                "cycles": yearly_cycles,
            },
            status=status.HTTP_200_OK,
        )


    def get(self, request):
        cycle = (
            MenstrualCycle.objects
            .filter(user=request.user)
            .order_by("-created_at")
            .first()
        )

        if not cycle:
            return Response(
                {"detail": "No cycle data found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        year = date.today().year

        analytics = CycleAnalytics(
            start_date=cycle.last_period_start_date,
            cycle_length=cycle.cycle_length_days,
            period_length=cycle.period_duration_days,
        )

        yearly_cycles = analytics.generate_year(year)

        return Response({
            "year": year,
            "assumption": "Cycle length and duration assumed constant",
            "input": {
                    "last_period_start_date": cycle.last_period_start_date,
                    "cycle_length_days": cycle.cycle_length_days,
                    "period_duration_days": cycle.period_duration_days,
                },
            "cycles": yearly_cycles,
        })



class SymptomOptionsView(APIView):
    """
    Returns a list of all active Symptom Categories and their tags.
    Includes both System Tags (global) and Custom Tags (created by this user).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        categories = SymptomCategory.objects.filter(is_active=True).order_by('order')
         
        serializer = SymptomCategorySerializer(
            categories, 
            many=True, 
            context={'request': request}
        )
        
        # 3. Return Response
        return Response(serializer.data, status=status.HTTP_200_OK)


# 2. Add New Custom Tag (APIView Version)
class CreateCustomTagView(APIView):
    """
    Allows a user to create a new custom symptom tag under a specific category.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CreateCustomTagSerializer(
            data=request.data, 
            context={'request': request}
        )

        # 2. Validate
        if serializer.is_valid():
            # 3. Save (The create() method in the serializer handles user assignment)
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        
        # 4. Handle Errors
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CycleDailyLogListCreateView(APIView):
    """
    GET: Retrieve the user's entire history of cycle logs.
    POST: Create a new daily log entry.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Filter: Optional query params for month/year could be added here
        logs = CycleDailyLog.objects.filter(user=request.user).order_by('-date')
        serializer = CycleDailyLogSerializer(logs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = CycleDailyLogSerializer(data=request.data)
        if serializer.is_valid():
            # Check if log already exists for this date to prevent duplicates
            date = serializer.validated_data.get('date')
            if CycleDailyLog.objects.filter(user=request.user, date=date).exists():
                 return Response(
                     {"error": "A log for this date already exists. Use PUT to update it."},
                     status=status.HTTP_400_BAD_REQUEST
                 )

            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# -----------------------------------------------------------------------------
# 2. Detail View (GET / PUT / DELETE)
# -----------------------------------------------------------------------------
class CycleDailyLogDetailView(APIView):
    """
    Manage a specific log entry by its ID.
    """
    permission_classes = [IsAuthenticated]

    def get_object(self, pk, user):
        # Helper method to ensure the log belongs to the requesting user
        return get_object_or_404(CycleDailyLog, pk=pk, user=user)

    def get(self, request, pk):
        log = self.get_object(pk, request.user)
        serializer = CycleDailyLogSerializer(log)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, pk):
        log = self.get_object(pk, request.user)
        serializer = CycleDailyLogSerializer(log, data=request.data, partial=True) # partial=True allows sending just fields to update
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        log = self.get_object(pk, request.user)
        log.delete()
        return Response({"message": "Log deleted successfully"}, status=status.HTTP_204_NO_CONTENT)
    