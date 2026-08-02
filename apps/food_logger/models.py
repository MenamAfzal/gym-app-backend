from django.db import models
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.utils import timezone

User = get_user_model()


class StaffRecipe(models.Model):
    """
    Staff-created recipe.
    """
    MEAL_CATEGORIES = [
        ('pre_workout', 'Pre Workout'),
        ('post_workout', 'Post Workout'),
        ('breakfast', 'Breakfast'),
        ('lunch', 'Lunch'),
        ('dinner', 'Dinner'),
        ('snack', 'Snack'),
    ]
    category = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateField(auto_now_add=True)

    def __str__(self):
        return self.category

    def total_nutrients(self):
        items = self.items.all()
        return {
            "calories": Sum(i.calories for i in items),
            "protein": Sum(i.protein for i in items),
            "carbs": Sum(i.carbs for i in items),
            "fats": Sum(i.fats for i in items),
        }


class StaffRecipeItem(models.Model):
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="staff_recipes", null=True, blank=True)
    recipe = models.ForeignKey(StaffRecipe, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=255)
    calories = models.FloatField(default=0)
    protein = models.FloatField(default=0)
    carbs = models.FloatField(default=0)
    fats = models.FloatField(default=0)
    serving_qty = models.CharField(null=True, blank=True, max_length=24)
    serving_info = models.CharField(null=True, blank=True, max_length=24)
    serving_unit = models.CharField(max_length=50, null=True, blank=True)
    serving_weight_grams = models.CharField(max_length=50, null=True, blank=True)
    cholesterol = models.CharField(max_length=50, null=True, blank=True)
    sodium = models.CharField(max_length=50, null=True, blank=True)
    sugars = models.CharField(max_length=50, null=True, blank=True)
    potassium =  models.CharField(max_length=50, null=True, blank=True)
    image = models.URLField(null=True, blank=True, max_length=500)
    description = models.TextField(blank=True)
    ingredients = models.TextField(blank=True, null=True)
    created_at = models.DateField(auto_now_add=True)

    saturated_fat = models.CharField(max_length=50, null=True, blank=True)
    fiber = models.CharField(max_length=50, null=True, blank=True)


    tag_name = models.CharField(max_length=50, null=True, blank=True)
    tag_id = models.CharField(max_length=50, null=True, blank=True)
    locale = models.CharField(max_length=50, null=True, blank=True)
    nix_item_id = models.CharField(max_length=50, null=True, blank=True)
    nix_brand_id = models.CharField(max_length=50, null=True, blank=True)



    def __str__(self):
        return f"{self.name} ({self.recipe})"

class UserNutritionGoal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="nutrition_goal")
    daily_calories = models.FloatField(default=0, null=True, blank=True)
    daily_protein = models.FloatField(default=0, null=True, blank=True)
    daily_carbs = models.FloatField(default=0, null=True, blank=True)
    daily_fat = models.FloatField(default=0, null=True, blank=True)
    date = models.DateField(default=timezone.localdate)
    base_goal = models.FloatField(default=0, null=True, blank=True)


    def __str__(self):
        return f"{self.user.email} - Goal"

class FoodSuggestion(models.Model):
    staff = models.ForeignKey(User, on_delete=models.CASCADE, related_name='suggestions')
    name = models.CharField(max_length=255)
    serving_info = models.CharField(max_length=100, null=True, blank=True)
    image = models.URLField(null=True, blank=True)
    calories = models.FloatField(default=0)
    protein = models.FloatField(default=0)
    carbs = models.FloatField(default=0)
    fats = models.FloatField(default=0)
    date = models.DateField(auto_now_add=True)
    cholesterol = models.CharField(max_length=50, null=True, blank=True)
    saturated_fat = models.CharField(max_length=50, null=True, blank=True)
    serving_weight_grams = models.CharField(max_length=50, null=True, blank=True)
    fiber = models.CharField(max_length=50, null=True, blank=True)
    serving_qty = models.CharField(null=True, blank=True, max_length=24)
    serving_unit = models.CharField(max_length=50, null=True, blank=True)
    potassium = models.CharField(max_length=50, null=True, blank=True)
    sodium = models.CharField(max_length=50, null=True, blank=True)
    sugars = models.CharField(max_length=50, null=True, blank=True)
    directions = models.TextField(blank=True, null=True)
    brand_name_item_name = models.CharField(max_length=255, null=True, blank=True)
    tag_name = models.CharField(max_length=50, null=True, blank=True)
    tag_id = models.CharField(max_length=50, null=True, blank=True)
    locale = models.CharField(max_length=50, null=True, blank=True)
    nix_item_id = models.CharField(max_length=50, null=True, blank=True)
    nix_brand_id = models.CharField(max_length=50, null=True, blank=True)



    def __str__(self):
        return self.name

# user sides
class UserRecipeItem(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="custom_recipe_items")
    name = models.CharField(max_length=255, null=True, blank=True)
    calories = models.FloatField(default=0)
    protein = models.FloatField(default=0)
    carbs = models.FloatField(default=0)
    fats = models.FloatField(default=0)
    serving_qty = models.CharField(null=True, blank=True, max_length=24)
    serving_unit = models.CharField(max_length=50, null=True, blank=True)
    serving_info = models.CharField(max_length=50, null=True, blank=True)
    serving_weight_grams = models.CharField(max_length=50, null=True, blank=True)
    cholesterol = models.CharField(max_length=50, null=True, blank=True)
    sodium = models.CharField(max_length=50, null=True, blank=True)
    sugars = models.CharField(max_length=50, null=True, blank=True)
    potassium = models.CharField(max_length=50, null=True, blank=True)
    image = models.URLField(null=True, blank=True)
    description = models.TextField(blank=True)
    ingredients = models.TextField(blank=True, null=True)
    created_at = models.DateField(auto_now_add=True)
    fiber = models.CharField(max_length=50, null=True, blank=True)
    saturated_fat = models.CharField(max_length=50, null=True, blank=True)

    tag_name = models.CharField(max_length=50, null=True, blank=True)
    tag_id = models.CharField(max_length=50, null=True, blank=True)
    locale = models.CharField(max_length=50, null=True, blank=True)
    nix_item_id = models.CharField(max_length=50, null=True, blank=True)
    nix_brand_id = models.CharField(max_length=50, null=True, blank=True)



    def __str__(self):
        return f"{self.name} (User Recipe Item)"



class CustomMeal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='custom_meals')
    name = models.CharField(max_length=255, null=True, blank=True)
    directions = models.TextField(blank=True, null=True)
    created_at = models.DateField(auto_now_add=True)


    def __str__(self):
        return self.name

    def total_nutrients(self):
        total = {"protein": 0, "carbs": 0, "fat": 0, "calories": 0}
        for item in self.items.all():
            total["protein"] += item.protein
            total["carbs"] += item.carbs
            total["fats"] += item.fats
            total["calories"] += item.calories
        return total


class MealItem(models.Model):
    meal = models.ForeignKey(CustomMeal, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=255, null=True, blank=True)
    image = models.URLField(null=True, blank=True)
    calories = models.FloatField(default=0)
    protein = models.FloatField(default=0)
    carbs = models.FloatField(default=0)
    fats = models.FloatField(default=0)
    date = models.DateField(auto_now_add=True)
    cholesterol = models.CharField(max_length=50, null=True, blank=True)
    saturated_fat = models.CharField(max_length=50, null=True, blank=True)
    serving_weight_grams = models.CharField(max_length=50, null=True, blank=True)
    fiber = models.CharField(max_length=50, null=True, blank=True)
    serving_qty = models.CharField(null=True, blank=True, max_length=24)
    serving_unit = models.CharField(max_length=50, null=True, blank=True)
    serving_info = models.CharField(max_length=50, null=True, blank=True)
    potassium = models.CharField(max_length=50, null=True, blank=True)
    sodium = models.CharField(max_length=50, null=True, blank=True)
    sugars = models.CharField(max_length=50, null=True, blank=True)
    brand_name_item_name = models.CharField(max_length=255, null=True, blank=True)
    tag_name = models.CharField(max_length=50, null=True, blank=True)
    tag_id = models.CharField(max_length=50, null=True, blank=True)
    locale = models.CharField(max_length=50, null=True, blank=True)
    nix_item_id = models.CharField(max_length=50, null=True, blank=True)
    nix_brand_id = models.CharField(max_length=50, null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.meal.name})"



class CustomFood(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='custom_food')
    is_custom_food = models.BooleanField(default=False)
    name = models.CharField(max_length=255, null=True, blank=True)
    image = models.URLField(null=True, blank=True)
    calories = models.FloatField(default=0)
    protein = models.FloatField(default=0)
    carbs = models.FloatField(default=0)
    fats = models.FloatField(default=0)
    serving_qty = models.CharField(null=True, blank=True, max_length=24)
    serving_unit = models.CharField(max_length=50, null=True, blank=True)
    serving_info = models.CharField(max_length=50, null=True, blank=True)
    potassium = models.CharField(max_length=50, null=True, blank=True)
    sodium = models.CharField(max_length=50, null=True, blank=True)
    sugars = models.CharField(max_length=50, null=True, blank=True)
    brand_name_item_name = models.CharField(max_length=255, null=True, blank=True)
    tag_name = models.CharField(max_length=50, null=True, blank=True)
    tag_id = models.CharField(max_length=50, null=True, blank=True)
    locale = models.CharField(max_length=50, null=True, blank=True)
    nix_item_id = models.CharField(max_length=50, null=True, blank=True)
    nix_brand_id = models.CharField(max_length=50, null=True, blank=True)
    date = models.DateField(default=timezone.localdate)

    cholesterol = models.CharField(max_length=50, null=True, blank=True)
    saturated_fat = models.CharField(max_length=50, null=True, blank=True)
    serving_weight_grams = models.CharField(max_length=50, null=True, blank=True)
    fiber = models.CharField(max_length=50, null=True, blank=True)


    def __str__(self):
        return f"{self.name} ({self.user.email})"


class LoggedMeal(models.Model):
    MEAL_TYPES = [
        ('breakfast', 'Breakfast'),
        ('lunch', 'Lunch'),
        ('dinner', 'Dinner'),
        ('snack', 'Snack'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='logged_meals')
    meal_type = models.CharField(max_length=50, choices=MEAL_TYPES)

    total_calories = models.FloatField(default=0)
    total_protein = models.FloatField(default=0)
    total_carbs = models.FloatField(default=0)
    total_fats = models.FloatField(default=0)

    created_at = models.DateField(default=timezone.now)

    def update_totals(self):
        items = self.items.all()
        self.total_calories = sum(i.calories for i in items)
        self.total_protein = sum(i.protein for i in items)
        self.total_carbs = sum(i.carbs for i in items)
        self.total_fats = sum(i.fats for i in items)
        self.save()

    def __str__(self):
        return f"{self.user.username} - {self.meal_type} ({self.created_at})"


class LoggedMealItem(models.Model):
    logged_meal = models.ForeignKey(LoggedMeal, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=255)
    calories = models.FloatField(default=0)
    protein = models.FloatField(default=0)
    carbs = models.FloatField(default=0)
    fats = models.FloatField(default=0)
    date = models.DateField(auto_now_add=True)
    logged_serving_qty = models.FloatField(null=True, blank=True, max_length=24)

    # optional references
    staff_recipe_item = models.ForeignKey(
        'StaffRecipeItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='logged_staff_items'
    )
    user_recipe_item = models.ForeignKey(
        'UserRecipeItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='logged_user_recipe_items'
    )
    suggestion = models.ForeignKey(
        'FoodSuggestion', on_delete=models.SET_NULL, null=True, blank=True, related_name='logged_suggestion_items'
    )

    meal = models.ForeignKey(
        'MealItem', on_delete=models.SET_NULL, null=True, blank=True, related_name='logged_meal_items'
    )
    food = models.ForeignKey(
        'CustomFood', on_delete=models.SET_NULL, null=True, blank=True, related_name='logged_food_items'
    )

    def __str__(self):
        return f"{self.name} ({self.logged_meal.meal_type})"


class FavoriteStaffRecipes(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites')
    staff_recipe = models.ForeignKey(StaffRecipeItem, on_delete=models.CASCADE, related_name='favorited_by')
    is_favorite = models.BooleanField(default=True)
    date = models.DateField(default=timezone.localdate)

    class Meta:
        unique_together = ('user', 'staff_recipe')

    def __str__(self):
        return f"{self.user.email} - {self.staff_recipe.name} (Favorite: {self.is_favorite})"


class UserMedication(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='medications')
    medicine_name = models.CharField(max_length=255)
    purpose = models.CharField(max_length=255, help_text="e.g., Blood Pressure, Muscle Recovery")
    dosage = models.CharField(max_length=100, help_text="e.g., 10mg, 2 scoops")
    frequency = models.CharField(
        max_length=50,
        choices=[
            ('Daily', 'Daily'),
            ('Twice a Day', 'Twice a Day'),
            ('Weekly', 'Weekly'),
            ('As Needed', 'As Needed'),
            ('Other', 'Other')
        ]
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Leave blank if taken indefinitely")
    is_currently_active = models.BooleanField(default=True)
    prescribed_by = models.CharField(max_length=255, null=True, blank=True)
    special_instructions = models.TextField(null=True, blank=True, help_text="e.g., Take with food")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} - {self.medicine_name}"
