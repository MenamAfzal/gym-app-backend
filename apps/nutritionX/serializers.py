from datetime import timedelta
import datetime
from rest_framework import serializers
from .models import CustomBeverage, MealLogs, FoodEntry, NutritionGoal, WaterIntake, DrinkNutrients
from django.db import transaction
from django.contrib.auth import get_user_model

User = get_user_model()


class FoodEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = FoodEntry
        fields = "__all__"


class MealLogSerializer(serializers.ModelSerializer):
    foods = FoodEntrySerializer(many=True, read_only=True)
    total_calories = serializers.CharField(read_only=True)
    total_protein = serializers.CharField(read_only=True)
    total_carbs = serializers.CharField(read_only=True)
    total_fat = serializers.CharField(read_only=True)
    user = serializers.CharField(source="user.email", read_only=True)


    class Meta:
        model = MealLogs
        fields = "__all__"


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email"]


class WaterIntakeSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = WaterIntake
        fields = ["id", "user", "date", "amount_ml"]

class NutritionGoalSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())

    class Meta:
        model = NutritionGoal
        fields = [
           'id',
           'user',
           'water_intake_goal_ml',
           'calories_goal_kcal',
           'protein_goal_g',
           'carbs_goal_g',
           'fat_goal_g',
           'is_active',
           'created_at',
           'updated_at',
           'base_water_intake_goal_ml', 
        ]

class BulkNutritionGoalSerializer(serializers.Serializer):
    goals = serializers.ListField(child=serializers.DictField())

    def create(self, validated_data):
        goals_data = validated_data["goals"]
        created_objs = []

        User = get_user_model()

        for goal_data in goals_data:
            user_id = goal_data.get("user")
            if not user_id:
                raise serializers.ValidationError("Each goal must include 'user'.")
            user = User.objects.get(id=user_id)

            # Deactivate current active goal if exists
            old_goal = NutritionGoal.objects.filter(user=user, is_active=True).first()
            if old_goal:
                old_goal.is_active = False
                old_goal.save()

            # Create new goal
            new_goal = NutritionGoal.objects.create(
                user=user,
                **{k: v for k, v in goal_data.items() if k != "user"},
                is_active=True
            )
            created_objs.append(new_goal)

        return created_objs
    
class CustomBeverageSerializer(serializers.ModelSerializer):
    image = serializers.URLField(read_only=True)

    class Meta:
        model = CustomBeverage
        fields = ['id', 'user', 'name', 'type', 'image', 'created_at']
        read_only_fields = ['user', 'created_at']


class DrinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = DrinkNutrients
        fields = "__all__"


class NutritionGoalSerializer(serializers.ModelSerializer):
    """Serializer for the macro limits themselves."""
    class Meta:
        model = NutritionGoal
        fields = [
            'id', 'calories_goal_kcal', 'protein_goal_g', 'carbs_goal_g', 
            'fat_goal_g', 'water_intake_goal_ml', 'is_active', 
            'created_at', 'updated_at'
        ]

class ClientMacroHistorySerializer(serializers.ModelSerializer):
    """Main Serializer for the User and their split Macro History."""
    current_limits = serializers.SerializerMethodField()
    historical_limits = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'role', 
            'current_limits', 'historical_limits'
        ]

    def get_current_limits(self, obj):
        active_goal = next((goal for goal in getattr(obj, 'all_goals', []) if goal.is_active), None)
        if active_goal:
            return NutritionGoalSerializer(active_goal).data
        return None

    def get_historical_limits(self, obj):
        history = [goal for goal in getattr(obj, 'all_goals', []) if not goal.is_active]
        return NutritionGoalSerializer(history, many=True).data
    