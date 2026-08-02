from django.contrib import admin
from apps.food_logger.models import *
# Register your models here.

admin.site.register(StaffRecipe)
admin.site.register(StaffRecipeItem)
admin.site.register(CustomMeal)
admin.site.register(MealItem)
admin.site.register(LoggedMeal)
admin.site.register(LoggedMealItem)
admin.site.register(UserNutritionGoal)
admin.site.register(FoodSuggestion)
admin.site.register(CustomFood)

