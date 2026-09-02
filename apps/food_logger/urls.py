from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    FoodLogGetApi, FoodSuggestionListCreateAPIView, StaffClientFoodLogListView, StaffRecipeItemUpdateAPIView, StaffRecipeListCreateAPIView, StaffRecipeItemListAPIView, StaffRecipeUpdateAPIView,
    UserRecipeItemListCreateAPIView, NutritionGoalBulkCreateAPIView, CustomMealView, LogFoodAPIView, CustomFoodApiView,
    GetAllFoodApiView, UserFavoriteStaffRecipeAPIView, AddFoodToMealView, LoggedMealDatesAPIView, UserMedicationViewSet,
    AnalyzeFoodAPIView
)
from apps.nutritionX.views import WaterIntakeAPIView, ClientMacroHistoryListView

router = DefaultRouter()
router.register(r'medications', UserMedicationViewSet, basename='user-medication')
urlpatterns = [ 
    
    path('analyze-food/', AnalyzeFoodAPIView.as_view(), name='food-analyze-food'),
    path('scan-meal/', AnalyzeFoodAPIView.as_view(), name='food-scan-meal'),
    path('scan-food/', AnalyzeFoodAPIView.as_view(), name='food-scan-food'),
    path('scan/', AnalyzeFoodAPIView.as_view(), name='food-scan'),


    # -------------------  Staff Interaction
    path('suggestions/', FoodSuggestionListCreateAPIView.as_view()),
    path('staff-search/', AddFoodToMealView.as_view(), name='search-meals'),
    path('nutrition/add-food/', AddFoodToMealView.as_view(), name='add-food'),
    path('nutrition/water-intake/', WaterIntakeAPIView.as_view(), name='food-water-intake'),

    path('staff-recipes/', StaffRecipeListCreateAPIView.as_view()),
    path('staff-recipes-items/', StaffRecipeItemListAPIView.as_view(), name='staff-recipe-items'),
    path('add-client-goals/', NutritionGoalBulkCreateAPIView.as_view(), name='add-goals'),
    path('goals/', NutritionGoalBulkCreateAPIView.as_view(), name='goals'),

    # -------------------- client ------------------
    path('add-client-recipes/', UserRecipeItemListCreateAPIView.as_view(), name='staff-recipe-items'),
    path('custom-meals/', CustomMealView.as_view(), name='custom-meals'),

    path('custom-meals/<meal_id>/', CustomMealView.as_view(), name='update-custom-meals'),

    path("log-food/", LogFoodAPIView.as_view(), name="log-food"),
    path("custom-food/", CustomFoodApiView.as_view(), name="custom-log-food"),
    path("get-all-foods/", GetAllFoodApiView.as_view(), name="custom-log-food"),

    path("client-recipe-favorite/", UserFavoriteStaffRecipeAPIView.as_view(), name="custom-log-food"),
    path("logged-dates/",LoggedMealDatesAPIView.as_view(), name="logged-dates"),
    path('staff-recipie-update/', StaffRecipeItemUpdateAPIView.as_view(), name='staff-recipe-item-update'),
    path('category-update/<int:recipe_id>/', StaffRecipeUpdateAPIView.as_view(), name='staff-recipe-update'),
    path('get-logged-meals/', FoodLogGetApi.as_view(), name='get-logged-meals'),

    path('staff/food-logs/', StaffClientFoodLogListView.as_view(), name='staff-food-logs'),
    path('clients/macro-limits/', ClientMacroHistoryListView.as_view(), name='food-client-macro-limits'),
]

urlpatterns += router.urls
