from django.db import models

from django.contrib.auth import get_user_model
from core_models.mixins.tenant_mixin import TenantMixin
from core_models.mixins.timestamps import TimestampMixin as TimeStampMixin

User = get_user_model()




class MealLogs(TimeStampMixin, TenantMixin):
    MEAL_TYPES = (
    ("Breakfast", "Breakfast"),
    ("Lunch", "Lunch"),
    ("Dinner", "Dinner"),
    ("Snack", "Snack"),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="meal_logs")
    meal_type = models.CharField(max_length=20, choices=MEAL_TYPES)
    date = models.DateField()

    class Meta:
        unique_together = ("user", "meal_type", "date")

    def __str__(self):
        return f"{self.user} - {self.meal_type} ({self.date})"

    # @property
    # def total_calories(self):
    #     return sum(int(float(food.calories)) or 0 for food in self.foods.all())
    #
    # @property
    # def total_protein(self):
    #     return sum(int(float(food.protein)) or 0 for food in self.foods.all())
    #
    # @property
    # def total_carbs(self):
    #     return sum(int(float(food.carbs)) or 0 for food in self.foods.all())
    #
    # @property
    # def total_fat(self):
    #     return sum(int(float(food.fat)) or 0 for food in self.foods.all())



class FoodEntry(TimeStampMixin, TenantMixin):
    food = models.ForeignKey(MealLogs, on_delete=models.CASCADE, related_name="foods", null=True, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="food_entries", null=True, blank=True)
    date = models.DateField(auto_now_add=True)
    serving_info = models.CharField(max_length=100, null=True, blank=True)
    food_name = models.CharField(max_length=100, null=True, blank=True)
    is_custom_food = models.BooleanField(default=False)
    serving_qty = models.CharField(null=True, blank=True, max_length=24)
    serving_unit = models.CharField(max_length=50, null=True, blank=True)
    serving_weight_grams = models.CharField(max_length=50, null=True, blank=True)
    saturated_fat = models.CharField(max_length=50, null=True, blank=True)
    cholesterol = models.CharField(max_length=50, null=True, blank=True)
    sodium = models.CharField(max_length=50, null=True, blank=True)
    total_carbohydrate = models.CharField(max_length=50, null=True, blank=True)
    dietary_fiber = models.CharField(max_length=50, null=True, blank=True)
    sugars = models.CharField(max_length=50, null=True, blank=True)
    calories =  models.CharField(max_length=50, null=True, blank=True)
    potassium =  models.CharField(max_length=50, null=True, blank=True)
    protein =  models.CharField(max_length=50, null=True, blank=True)
    fat =  models.CharField(max_length=50, null=True, blank=True)
    carbs =  models.CharField(max_length=50, null=True, blank=True)
    tag_name = models.CharField(max_length=50, null=True, blank=True)
    tag_id = models.CharField(max_length=50, null=True, blank=True)
    locale = models.CharField(max_length=50, null=True, blank=True)
    image = models.URLField(null=True, blank=True)
    nix_item_id = models.CharField(max_length=50, null=True, blank=True)
    nix_brand_id = models.CharField(max_length=50, null=True, blank=True)
    brand_name_item_name = models.CharField(max_length=50, null=True, blank=True)


    def __str__(self):
        return f"{self.food_name} ({self.calories} kcal)"




class WaterIntake(TimeStampMixin, TenantMixin):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="water_intakes")
    date = models.DateField()
    amount_ml = models.FloatField(default=0)

    class Meta:
        unique_together = ("user", "date")

    def __str__(self):
        return f"{self.user.username} - {self.amount_ml} amount_ml ({self.date})"



class NutritionGoal(TimeStampMixin, TenantMixin):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="nutrition_goals")

    water_intake_goal_ml = models.CharField(max_length=100, null=True, blank=True)
    calories_goal_kcal = models.CharField(max_length=100, null=True, blank=True)
    protein_goal_g = models.CharField(max_length=100, null=True, blank=True)
    carbs_goal_g = models.CharField(max_length=100, null=True, blank=True)
    fat_goal_g = models.CharField(max_length=100, null=True, blank=True)
    base_water_intake_goal_ml = models.CharField(max_length=100, null=True, blank=True)

    is_active = models.BooleanField(default=True)  # Only one active goal per user

    def __str__(self):
        status = "Active" if self.is_active else "History"
        return f"{self.user.email} - NutritionGoal ({status})"
    

class DailyNutritionProgress(TimeStampMixin, TenantMixin):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="daily_progress")
    goal = models.ForeignKey(NutritionGoal, on_delete=models.CASCADE, related_name="progress", null=True, blank=True)
    date = models.DateField()

    water_consumed_ml = models.FloatField(default=0)
    calories_consumed_kcal = models.FloatField(default=0)
    protein_consumed_g = models.FloatField(default=0)
    carbs_consumed_g = models.FloatField(default=0)
    fat_consumed_g = models.FloatField(default=0)

    class Meta:
        unique_together = ("user", "date")

    def __str__(self):
        return f"{self.user.email} - {self.date}"
        

class DrinkNutrients(TimeStampMixin, TenantMixin):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="nutrients")
    drink_take_ml = models.CharField(max_length=50, null=True, blank=True)
    name = models.CharField(max_length=255)

    serving_qty = models.FloatField()
    serving_unit = models.CharField(max_length=50)
    serving_weight_grams = models.FloatField()
    sodium = models.CharField(max_length=50, null=True, blank=True)
    fat = models.CharField(max_length=50, null=True, blank=True)
    potassium = models.CharField(max_length=50, null=True, blank=True)
    water_grams = models.FloatField()
    water_fraction = models.FloatField(help_text="Water % of total weight")
    calories = models.FloatField(null=True, blank=True)
    date= models.DateField()

    def __str__(self):
        return self.name

 
        
    
class CustomBeverage(TimeStampMixin, TenantMixin):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="custom_beverages")
    name = models.CharField(max_length=100, null=True, blank=True)
    type = models.CharField(max_length=100, null=True, blank=True)
    image = models.URLField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} - {self.user.email}"
    
 
