import json

from django.utils import timezone
from rest_framework import serializers
from .models import FoodSuggestion, StaffRecipeItem, StaffRecipe, UserRecipeItem, UserNutritionGoal, MealItem, \
    CustomMeal, LoggedMeal, LoggedMealItem, CustomFood, FavoriteStaffRecipes, UserMedication
from apps.users.models import User
from datetime import datetime
from .utils import save_recipe_image, get_full_recipe_image_url


class ImageOrURLField(serializers.Field):
    """
    Accepts both uploaded files (MultiPart) and string URLs / base64 payloads,
    and returns fully qualified absolute image URL in representation.
    """
    def to_internal_value(self, data):
        return data

    def to_representation(self, value):
        return get_full_recipe_image_url(value, self.context.get('request'))



class Userserializer(serializers.ModelSerializer):
    fullname = serializers.SerializerMethodField()
    class Meta:
        model = User
        fields = ['id', 'email', 'fullname']

    def get_fullname(self, obj):
        try:
            if hasattr(obj, 'profile') and obj.profile:
                val = f"{obj.profile.first_name or ''} {obj.profile.last_name or ''}".strip()
                if val:
                    return val
        except Exception:
            pass
        val_user = f"{obj.first_name or ''} {obj.last_name or ''}".strip()
        return val_user if val_user else obj.email


class NutritionXGoalSerializer(serializers.ModelSerializer):
    user = Userserializer(read_only=True)
    calories_goal_kcal = serializers.FloatField(source='daily_calories', read_only=True)
    protein_goal_g = serializers.FloatField(source='daily_protein', read_only=True)
    carbs_goal_g = serializers.FloatField(source='daily_carbs', read_only=True)
    fat_goal_g = serializers.FloatField(source='daily_fat', read_only=True)
    water_goal_ml = serializers.FloatField(source='daily_water', read_only=True)

    class Meta:
        model = UserNutritionGoal
        fields = [
            'id', 'user', 'calories_goal_kcal', 'protein_goal_g', 
            'carbs_goal_g', 'fat_goal_g', 'water_goal_ml', 'date', 'base_goal'
        ]

class NutritionGoalSerializer(serializers.ModelSerializer):
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    calories_goal_kcal = serializers.FloatField(source='daily_calories', required=False, allow_null=True)
    protein_goal_g = serializers.FloatField(source='daily_protein', required=False, allow_null=True)
    carbs_goal_g = serializers.FloatField(source='daily_carbs', required=False, allow_null=True)
    fat_goal_g = serializers.FloatField(source='daily_fat', required=False, allow_null=True)
    water_goal_ml = serializers.FloatField(source='daily_water', required=False, allow_null=True)

    class Meta:
        model = UserNutritionGoal
        fields = [
            'id', 'user', 'calories_goal_kcal', 'protein_goal_g', 
            'carbs_goal_g', 'fat_goal_g', 'water_goal_ml', 'date', 'base_goal'
        ]

class BulkNutritionGoalSerializer(serializers.Serializer):
    goals = NutritionGoalSerializer(many=True)

    def create(self, validated_data):
            goals_data = validated_data["goals"]
            results = []

            for goal_data in goals_data:
                user = goal_data["user"]
                date = goal_data.get("date", timezone.now().date())
                if isinstance(date, datetime):
                    date = date.date()
                goal, _ = UserNutritionGoal.objects.update_or_create(
                    user=user,
                    date=date,
                    defaults=goal_data
                )
                results.append(goal)
            return results

class FoodSuggestionSerializer(serializers.ModelSerializer):
    staff = Userserializer(read_only=True)

    class Meta:
        model = FoodSuggestion
        fields = '__all__'
        read_only_fields = ['staff']

class StaffRecipeItemSerializer(serializers.ModelSerializer):
    created_by = Userserializer(read_only=True)
    image = ImageOrURLField(required=False, allow_null=True)

    class Meta:
        model = StaffRecipeItem
        exclude = ['recipe']

class StaffRecipeListSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffRecipe
        fields = ['id', 'category', 'created_at']

class StaffRecipeSerializer(serializers.ModelSerializer):
    items = StaffRecipeItemSerializer(many=True, required=False)

    class Meta:
        model = StaffRecipe
        fields = ['id', 'category', 'created_at', 'items']
        read_only_fields = ['created_at']

    def to_internal_value(self, data):
        if hasattr(data, 'dict'):
            clean_data = data.dict()
        elif hasattr(data, 'copy'):
            clean_data = dict(data.copy())
        else:
            clean_data = dict(data)

        if 'items' in clean_data and isinstance(clean_data['items'], str):
            try:
                import json
                clean_data['items'] = json.loads(clean_data['items'])
            except Exception:
                pass

        if 'items' not in clean_data or not clean_data['items']:
            flat_item = {}
            for field in [
                'name', 'calories', 'protein', 'carbs', 'fats', 'serving_qty',
                'serving_info', 'serving_unit', 'serving_weight_grams',
                'cholesterol', 'sodium', 'sugars', 'potassium', 'saturated_fat', 'fiber',
                'description', 'ingredients', 'image', 'tag_name', 'tag_id', 'locale'
            ]:
                if field in clean_data:
                    flat_item[field] = clean_data[field]
            if 'fat' in clean_data and 'fats' not in clean_data:
                flat_item['fats'] = clean_data['fat']
            if flat_item:
                clean_data['items'] = [flat_item]

        return super().to_internal_value(clean_data)

    def create(self, validated_data):
        """
        Custom create method for multipart/form-data + nested JSON items.
        Stores uploaded or linked images on backend storage and saves image URL to DB.
        """
        request = self.context.get('request')
        user = request.user if request else None

        category_val = validated_data.get('category')
        recipe, created = StaffRecipe.objects.get_or_create(
            category=category_val,
        )

        items_data = validated_data.pop('items', [])
        if not items_data and request:
            items_raw = request.data.get('items')
            if items_raw:
                if isinstance(items_raw, list):
                    items_data = items_raw
                else:
                    try:
                        import json
                        items_data = json.loads(items_raw)
                    except Exception:
                        pass

        # Check for top-level shared file or URL from request
        shared_file = None
        shared_url = None
        if request:
            if hasattr(request, 'FILES'):
                shared_file = (
                    request.FILES.get('image')
                    or request.FILES.get('file')
                    or request.FILES.get('photo')
                )
            if hasattr(request, 'data'):
                raw_top_img = request.data.get('image')
                if isinstance(raw_top_img, str) and raw_top_img.strip():
                    shared_url = raw_top_img.strip()

        for item_data in items_data:
            # Determine image for this item (item-specific image > shared file > shared URL)
            raw_img = item_data.pop('image', None) or shared_file or shared_url
            saved_image_url = save_recipe_image(raw_img, request=request) if raw_img else None

            created_item = StaffRecipeItem.objects.create(
                recipe=recipe,
                created_by=user,
                image=saved_image_url,
                **item_data
            )
            recipe._created_item = created_item

        return recipe
# ------ User Recipe Serializer

class UserRecipeItemSerializer(serializers.ModelSerializer):
    image = ImageOrURLField(required=False, allow_null=True)

    class Meta:
        model = UserRecipeItem
        fields = "__all__"
        read_only_fields = ["user"]

    def create(self, validated_data):
        request = self.context.get("request")
        user = request.user if request else None

        raw_image = None
        if request and hasattr(request, 'FILES'):
            raw_image = (
                request.FILES.get('image')
                or request.FILES.get('file')
                or request.FILES.get('photo')
            )
        if not raw_image and request and hasattr(request, 'data'):
            raw_image = request.data.get('image')

        if raw_image:
            validated_data['image'] = save_recipe_image(raw_image, request=request)

        return UserRecipeItem.objects.create(
            user=user,
            **validated_data
        )

    def update(self, instance, validated_data):
        request = self.context.get("request")

        file_obj = None
        if request and hasattr(request, 'FILES'):
            file_obj = (
                request.FILES.get('image')
                or request.FILES.get('file')
                or request.FILES.get('photo')
            )

        _SENTINEL = object()
        image_data = _SENTINEL
        if request and hasattr(request, 'data') and 'image' in request.data:
            image_data = request.data.get('image')

        image_input = file_obj if file_obj is not None else image_data

        if image_input is not _SENTINEL:
            if image_input in [None, "", False]:
                instance.image = None
            else:
                instance.image = save_recipe_image(image_input, request=request)

        for attr, value in validated_data.items():
            if attr != 'image':
                setattr(instance, attr, value)
        instance.save()
        return instance

class FavoriteStaffRecipeSerializer(serializers.ModelSerializer):
    staff_recipe_name = serializers.CharField(source='staff_recipe.name', read_only=True)
    staff_recipe = StaffRecipeItemSerializer(read_only=True)

    class Meta:
        model = FavoriteStaffRecipes
        fields = ['id', 'staff_recipe', 'staff_recipe_name', 'is_favorite', 'date']

class MealItemSerializer(serializers.ModelSerializer):


    class Meta:
        model = MealItem
        fields = "__all__"
        extra_kwargs = {
            "meal": {"read_only": True},  # ✅ user auto-assigned from request
        }

class CustomMealSerializer(serializers.ModelSerializer):
    items = MealItemSerializer(many=True)

    class Meta:
        model = CustomMeal
        fields = "__all__"
        extra_kwargs = {
            "user": {"read_only": True},
        }

    def create(self, validated_data):

        items_data = validated_data.pop('items', [])
        user = self.context["request"].user
        existing_meal = CustomMeal.objects.filter(user=user, name=validated_data.get('name')).first()
        if existing_meal:
            raise serializers.ValidationError({
                "meal": [f"Meal '{existing_meal.name}' already exists for this user. Use a different name."]
            })

        meal = CustomMeal.objects.create(**validated_data)
        items = [MealItem(meal=meal, **item_data) for item_data in items_data]
        MealItem.objects.bulk_create(items)
        return meal

class UserFoodSerializer(serializers.ModelSerializer):
    user = Userserializer(read_only=True)
    class Meta:
        model = CustomFood
        fields = "__all__"
        read_only_fields = ["user"]

    def create(self, validated_data):
        user = self.context["request"].user

        return CustomFood.objects.create(
            user=user,
            **validated_data
        )


# ---------------- Helper serializers ----------------

def build_item_serializer(model_class):

    class BaseSerializer(serializers.ModelSerializer):
        image = serializers.SerializerMethodField()

        class Meta:
            model = model_class
            fields = [
                "id", "name", "protein", "carbs", "fats", "calories", "cholesterol",
                "saturated_fat", "serving_weight_grams", "fiber",
                "serving_qty", "serving_unit", "serving_info",
                "potassium", "sodium", "sugars", "image"
            ]

        def get_image(self, obj):
            return get_full_recipe_image_url(getattr(obj, 'image', None), self.context.get('request'))
    return BaseSerializer


HelperStaffRecipeItemSerializer = build_item_serializer(StaffRecipeItem)
HelperUserRecipeItemSerializer = build_item_serializer(UserRecipeItem)
HelperMealSerializer = build_item_serializer(MealItem)
HelperSuggestionSerializer = build_item_serializer(FoodSuggestion)
HelperCustomFoodSerializer = build_item_serializer(CustomFood)


class LoggedMealItemSerializer(serializers.ModelSerializer):
    staff_recipe_item = HelperStaffRecipeItemSerializer(read_only=True)
    user_recipe_item = HelperUserRecipeItemSerializer(read_only=True)
    meal = HelperMealSerializer(read_only=True)
    suggestion = HelperSuggestionSerializer(read_only=True)
    food = HelperCustomFoodSerializer(read_only=True)

    class Meta:
        model = LoggedMealItem
        fields = [
            "id", "name", "calories", "protein", "carbs", "fats", "date",
            "staff_recipe_item", "user_recipe_item", "meal", "suggestion","food", "logged_serving_qty"
        ]


class LoggedMealSerializer(serializers.ModelSerializer):
    items = LoggedMealItemSerializer(many=True, read_only=True)

    class Meta:
        model = LoggedMeal
        fields = [
            "meal_type", "created_at", "total_calories", "total_protein",
            "total_carbs", "total_fats", "items" 
        ]



class StaffRecipeItemUpdateSerializer(serializers.ModelSerializer):
    image = ImageOrURLField(required=False, allow_null=True)
    class Meta:
        model = StaffRecipeItem
        exclude = ['recipe', 'created_by']   
        read_only_fields = ['id', 'created_at']

    def update(self, instance, validated_data):
        request = self.context.get('request')

        file_obj = None
        if request and hasattr(request, 'FILES'):
            file_obj = (
                request.FILES.get('image')
                or request.FILES.get('file')
                or request.FILES.get('photo')
            )

        _SENTINEL = object()
        image_data = validated_data.pop('image', _SENTINEL)

        image_input = file_obj if file_obj is not None else image_data

        if image_input is not _SENTINEL:
            if image_input in [None, "", False]:
                instance.image = None
            else:
                saved_url = save_recipe_image(image_input, request=request)
                instance.image = saved_url

        # Update remaining fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance
    

class StaffRecipeUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffRecipe
        fields = ['category']    


class LoggedMealGetSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()

    class Meta:
        model = LoggedMeal
        fields = [
            "id",
            "meal_type", "created_at", "total_calories", "total_protein",
            "total_carbs", "total_fats", "items"
        ]

    def get_items(self, obj):
        # 1. Retrieve all items ordered by ID
        items = obj.items.all().order_by('id')
        
        final_output_list = []
        
        # Helper to track 'open' wrappers for Custom Meals
        open_wrappers_map = {} 

        for item in items:
            # --- Case A: Item is part of a Custom Meal ---
            if item.meal and item.meal.meal:
                cm_id = item.meal.meal.id
                meal_item_def_id = item.meal.id 

                if cm_id not in open_wrappers_map:
                    open_wrappers_map[cm_id] = []
                
                target_wrapper = None
                for wrapper in open_wrappers_map[cm_id]:
                    if meal_item_def_id not in wrapper['_logged_def_ids']:
                        target_wrapper = wrapper
                        break
                
                if not target_wrapper:
                    custom_meal_obj = item.meal.meal
                    target_wrapper = {
                        "id": item.id,  # This is just the ID of the first item
                        "name": custom_meal_obj.name,
                        "calories": 0.0,
                        "protein": 0.0,
                        "carbs": 0.0,
                        "fats": 0.0,
                        "date": item.date,
                        "staff_recipe_item": None,
                        "user_recipe_item": None,
                        "suggestion": None,
                        "food": None,
                        "logged_serving_qty": item.logged_serving_qty, 
                        "meal": [], 
                        "grouped_ids": [], # <--- NEW FIELD to store all IDs
                        "_logged_def_ids": set() 
                    }
                    
                    open_wrappers_map[cm_id].append(target_wrapper)
                    final_output_list.append(target_wrapper)

                # Add stats to wrapper
                target_wrapper['calories'] += item.calories
                target_wrapper['protein'] += item.protein
                target_wrapper['carbs'] += item.carbs
                target_wrapper['fats'] += item.fats
                
                # Add current item ID to the group list
                target_wrapper['grouped_ids'].append(item.id) # <--- COLLECT ID HERE

                # Add ingredient details
                serialized_ingredient = HelperMealSerializer(item.meal).data
                target_wrapper['meal'].append(serialized_ingredient)
                
                target_wrapper['_logged_def_ids'].add(meal_item_def_id)

            # --- Case B: Standalone Item ---
            else:
                data = LoggedMealItemSerializer(item).data
                # For consistency, you can add grouped_ids here too if you want, or handle in frontend
                # data['grouped_ids'] = [item.id] 
                final_output_list.append(data)

        # Cleanup internal keys
        for entry in final_output_list:
            if isinstance(entry, dict) and '_logged_def_ids' in entry:
                del entry['_logged_def_ids']
                
        return final_output_list


class UserMedicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserMedication
        fields = '__all__'
        read_only_fields = ['user']


class LoggedMealItemSerializer(serializers.ModelSerializer):
    """Serializes the individual food items eaten in the meal."""
    class Meta:
        model = LoggedMealItem
        fields = [
            'id', 'name', 'calories', 'protein', 'carbs', 
            'fats', 'logged_serving_qty'
        ]

class ClientFoodLogSerializer(serializers.ModelSerializer):
    """Main Serializer for the Meal and its owner."""

    client_id = serializers.IntegerField(source='user.id', read_only=True)
    client_email = serializers.CharField(source='user.email', read_only=True)
    client_first_name = serializers.CharField(source='user.profile.first_name', read_only=True)
    client_last_name = serializers.CharField(source='user.profile.last_name', read_only=True)
    
    items = LoggedMealItemSerializer(many=True, read_only=True)

    class Meta:
        model = LoggedMeal
        fields = [
            'id', 'client_id', 'client_email', 'client_first_name', 'client_last_name',
            'meal_type', 'total_calories', 'total_protein', 'total_carbs', 'total_fats',
            'created_at', 'items'
        ]
