from django.urls import path
from apps.nutritionX.views import AddFoodToMealView, ClientMacroHistoryListView, CustomBeverageAPIView, DailyProgressAPIView, NutritionGoalBulkCreateAPIView, UserDailyMealsView, CustomFoodEntryAPIView, UserDrinkHistoryAPIView, WaterIntakeAPIView

urlpatterns = [
    path("add-food/", AddFoodToMealView.as_view(), name="add-food"),
    path("daily-meals/", UserDailyMealsView.as_view(), name="daily-meals"),
    path("custom-meal/", CustomFoodEntryAPIView.as_view(), name="meal-log-create"),

    path("water-intake/", WaterIntakeAPIView.as_view(), name="water-intake"),
    path("goals/", NutritionGoalBulkCreateAPIView.as_view(), name="nutrition-goals"),
    path("add-custom-beverage/", CustomBeverageAPIView.as_view(), name="add-custom-beverage"),
    path('custom-beverages/<int:pk>/', CustomBeverageAPIView.as_view(), name='custom-beverage-detail'),
    path('user-drink-history/', UserDrinkHistoryAPIView.as_view(), name='user-drink-history'),
    path('progress/', DailyProgressAPIView.as_view(), name='daily-progress'),
    path('clients/macro-limits/', ClientMacroHistoryListView.as_view(), name='staff-client-macro-limits')

]