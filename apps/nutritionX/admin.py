from django.contrib import admin
from .models import *
# Register your models here.

admin.site.register(MealLogs)
admin.site.register(FoodEntry)
admin.site.register(WaterIntake)
admin.site.register(NutritionGoal)
 
admin.site.register(DrinkNutrients)
admin.site.register(DailyNutritionProgress)
 
admin.site.register(CustomBeverage)

 
