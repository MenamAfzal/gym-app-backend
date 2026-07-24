from django.urls import path
from .views import (
    CreateCustomTagView,
    CycleDailyLogDetailView,
    CycleDailyLogListCreateView,
    DailyReflectionAPIView,
    DailyReflectionDetailAPIView,
    FocusOptionDetailView,
    MenstrualCycleAPIView,
    SymptomOptionsView,
    TodayReflectionAPIView,
    ReflectionStreakAPIView,
    ReflectionAnalyticsAPIView,
    FocusOptionAPIView,
    AIQuoteAPIView,
)

urlpatterns = [

    # reflections listing + create
    path("reflections/", DailyReflectionAPIView.as_view(), name="reflections"),
    path("reflections/update/<int:pk>/", DailyReflectionAPIView.as_view(), name="patch-reflections"),

    # single reflection get/update
    path("reflections/<int:pk>/", DailyReflectionDetailAPIView.as_view(), name="reflection-detail"),

    # today's reflection
    path("reflections/today/", TodayReflectionAPIView.as_view(), name="reflection-today"),

    # streaks
    path("reflections/streaks/", ReflectionStreakAPIView.as_view(), name="reflection-streaks"),

    # analytics
    path("reflections/analytics/", ReflectionAnalyticsAPIView.as_view(), name="reflection-analytics"),

    # focus options
    path("focus-options/", FocusOptionAPIView.as_view(), name="focus-options"),
    path('focus-options/<int:pk>/', FocusOptionDetailView.as_view(), name='focus-options-detail'),

    # ai quote
    path("ai/quote/", AIQuoteAPIView.as_view(), name="ai-quote"),


    # menstrual cycle
    path("menstrual-cycle/", MenstrualCycleAPIView.as_view(), name="menstrual-cycle"),
    path('symptom-options/', SymptomOptionsView.as_view(), name='symptom-options'),
    path('symptom-tags/', CreateCustomTagView.as_view(), name='create-symptom-tag'),
    # Logs (Replaces the Router)
    path('cycle-logs/', CycleDailyLogListCreateView.as_view(), name='cycle-logs-list'),
    path('cycle-logs/<int:pk>/', CycleDailyLogDetailView.as_view(), name='cycle-logs-detail'),
]
