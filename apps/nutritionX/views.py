import re
from django.shortcuts import get_object_or_404
from rest_framework import filters, status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from django.contrib.auth import get_user_model
User = get_user_model()
def upload_image_to_s3(file, obj): return "http://mock-s3-url.com/image.jpg"
def delete_s3_file_threaded(url): pass
from .models import CustomBeverage, DailyNutritionProgress, MealLogs, FoodEntry, NutritionGoal, DrinkNutrients
from datetime import datetime, date
from django.utils.dateparse import parse_date
from .models import WaterIntake
from django.db import transaction, IntegrityError
from .serializers import BulkNutritionGoalSerializer, ClientMacroHistorySerializer, CustomBeverageSerializer, NutritionGoalSerializer, WaterIntakeSerializer, DrinkSerializer

from .nutritionx_service import NutritionXService
from .serializers import MealLogSerializer, FoodEntrySerializer
IsClientUser = IsAuthenticated
IsStaffUser = IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import generics
from django.db.models import Prefetch


class AddFoodToMealView(APIView):
    permission_classes = [IsAuthenticated, IsClientUser]

    def get(self, request):
        query = request.query_params.get('query')
        if not query:
                return Response({"error": "query are required"}, status=status.HTTP_400_BAD_REQUEST)

        nutrition_x = NutritionXService()
        response = nutrition_x.nutrition_instant_search(query)
        if response.status_code == 200:
            return Response(response.json(), status=status.HTTP_200_OK)
        else:
            return Response({"error": response.text}, status=status.HTTP_400_BAD_REQUEST)

    def post(self, request):
        meal_type = request.data.get("meal_type")
        date = request.data.get("date")
        food = request.data.get("food")
        if not meal_type or not date or not food:
            return Response({"error": "meal_type, date, and query are required"}, status=status.HTTP_400_BAD_REQUEST)
        meal_log, _ = MealLogs.objects.get_or_create(
            user=request.user, meal_type=meal_type, date=date
        )
        if meal_log:
            for item in food:
                FoodEntry.objects.create(
                    food=meal_log,
                    food_name=item.get("food_name", ""),
                    user=request.user,
                    serving_qty = item.get("serving_qty", ""),
                    serving_unit = item.get("serving_unit", ""),
                    serving_weight_grams = item.get("serving_weight_grams", ""),
                    saturated_fat = item.get("saturated_fat", ""),
                    cholesterol = item.get("cholesterol", ""),
                    sodium = item.get("sodium", ""),
                    total_carbohydrate = item.get("total_carbohydrate", ""),
                    dietary_fiber = item.get("dietary_fiber", ""),
                    sugars = item.get("sugars", ""),
                    protein = item.get("protein", ""),
                    potassium = item.get("potassium", ""),
                    calories = item.get("calories", ""),
                    fat = item.get("total_fat", ""),
                    carbs = item.get("carbs", ""),
                    tag_name = item.get("tag_name", ""),
                    tag_id = item.get("tag_id", ""),
                    locale = item.get("locale", ""),
                    image = item.get("image", ""),
                    nix_item_id = item.get("nix_item_id", ""),
                    nix_brand_id = item.get("nix_brand_id", ""),
                    brand_name_item_name = item.get("brand_name_item_name", ""),

                )
            return Response(MealLogSerializer(meal_log).data, status=200)
        return Response({"message": "Error during Food saving"}, status=status.HTTP_200_OK)

    def delete(self, request):
        food_id = request.query_params.get("food_id")
        if not food_id:
            return Response({"error": "Please provide 'food_id'."}, status=status.HTTP_400_BAD_REQUEST)

        deleted_count, _ = FoodEntry.objects.filter(id=food_id, user=request.user).delete()
        if deleted_count == 0:
            return Response({"error": "Meal Food not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response({"message": "Meal Food deleted successfully."}, status=status.HTTP_200_OK)




class UserDailyMealsView(APIView):
    permission_classes = [IsAuthenticated, IsClientUser]

    def get(self, request):
        date = request.query_params.get("date")
        if not date:
            return Response({"error": "date query param is required"}, status=status.HTTP_400_BAD_REQUEST)

        meals_query = MealLogs.objects.filter(user=request.user)
        if date:
            meals_query = meals_query.filter(date=date)

        meals = meals_query.prefetch_related("foods")

        if not meals.exists():
            return Response({"error": "No meals found for this date"}, status=status.HTTP_404_NOT_FOUND)

        # food_entries = FoodEntry.objects.filter(food__in=meals)
        # totals = food_entries.aggregate(
        #     total_calories=Sum(Cast("calories", FloatField())),
        #     total_protein=Sum(Cast("protein", FloatField())),
        #     total_carbs=Sum(Cast("carbs", FloatField())),
        #     total_fat=Sum(Cast("fat", FloatField())),
        # )

        daily_data = {
            "meals": MealLogSerializer(meals, many=True).data,
            # "total_calories": totals["total_calories"] or 0,
            # "total_protein": totals["total_protein"] or 0,
            # "total_carbs": totals["total_carbs"] or 0,
            # "total_fat": totals["total_fat"] or 0,
        }

        return Response(daily_data, status=status.HTTP_200_OK)



class CustomFoodEntryAPIView(APIView):
    permission_classes = [IsAuthenticated, IsClientUser]

    def get(self, request):
        queryset = FoodEntry.objects.filter(user=request.user,
                                            is_custom_food=True, food__isnull=True)
        serializer = FoodEntrySerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        """
            Create or link food entries.
                - If 'food' is null → create a custom meal.
                - If 'food' is provided → link to a MealLog.
                - If same food is already in that MealLog → skip duplicate.
                - If same food exists in another MealLog → create a copy (keep history).
        """
        data = request.data.copy()
        data["user"] = request.user.id

        meal_log_name = data.get("food")
        food_name = data.get("food_name")
        date = data.get("date")
        if date is not None and food_name is not None:
            meal_log, _ = MealLogs.objects.get_or_create(
                user=request.user, meal_type=meal_log_name, date=date
            )
            data["food"] = meal_log.id

        if not meal_log_name:
            serializer = FoodEntrySerializer(data=data)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        existing_custom_food = FoodEntry.objects.filter(
            food_name__iexact=food_name,
            food__isnull=True,
            user=request.user,

        ).first()

        if FoodEntry.objects.filter(
                food=meal_log,
                food_name__iexact=food_name,

        ).exists():
            return Response(
                {"message": f"{food_name} already exists in this meal."},
                status=status.HTTP_200_OK,
            )

        if existing_custom_food:

            new_food = FoodEntry.objects.create(
                food=meal_log,
                user=request.user,
                is_custom_food=existing_custom_food.is_custom_food,
                serving_info=existing_custom_food.serving_info,
                food_name=existing_custom_food.food_name,
                serving_qty=existing_custom_food.serving_qty,
                serving_unit=existing_custom_food.serving_unit,
                serving_weight_grams=existing_custom_food.serving_weight_grams,
                saturated_fat=existing_custom_food.saturated_fat,
                cholesterol=existing_custom_food.cholesterol,
                sodium=existing_custom_food.sodium,
                total_carbohydrate=existing_custom_food.total_carbohydrate,
                dietary_fiber=existing_custom_food.dietary_fiber,
                sugars=existing_custom_food.sugars,
                potassium=existing_custom_food.potassium,
                calories=existing_custom_food.calories,
                protein=existing_custom_food.protein,
                fat=existing_custom_food.fat,
                carbs=existing_custom_food.carbs,
                tag_name=existing_custom_food.tag_name,
                tag_id=existing_custom_food.tag_id,
                locale=existing_custom_food.locale,
                image=existing_custom_food.image,
                nix_item_id=existing_custom_food.nix_item_id,
                nix_brand_id=existing_custom_food.nix_brand_id,
                brand_name_item_name=existing_custom_food.brand_name_item_name,
            )
            serializer = FoodEntrySerializer(new_food)
            return Response(
                {"message": "Custom food reused for new meal.", "data": serializer.data},
                status=status.HTTP_201_CREATED,
            )

        serializer = FoodEntrySerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


ML_TO_OZ = 29.5735  # 1 oz = 29.5735 ml

class WaterIntakeAPIView(APIView):
    permission_classes = [IsAuthenticated]  # Add IsClientUser if needed

    def get(self, request):
        """Get progress for a specific date or today if not provided"""
        date_param = request.query_params.get("date")
        query_date = None
        if date_param:
            try:
                query_date = datetime.strptime(date_param, "%Y-%m-%d").date()
            except ValueError:
                return Response(
                    {"error": "Invalid date format. Use YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            query_date = date.today()

        # Check if this is a historical date (past date)
        is_historical_date = query_date < date.today()
        
        # Get active goal for current/future dates, or goal that was active on historical date
        if is_historical_date:
            # For historical dates, get the goal that was active on that date
            goal = NutritionGoal.objects.filter(
                user=request.user,
                created_at__lte=query_date
            ).order_by("-created_at").first()
            
            # Also check if progress exists and has a goal
            existing_progress = DailyNutritionProgress.objects.filter(
                user=request.user,
                date=query_date
            ).first()
            
            if existing_progress and existing_progress.goal:
                # Use the goal from the progress record (preserves historical data)
                goal = existing_progress.goal
        else:
            # For today or future dates, use current active goal
            goal = NutritionGoal.objects.filter(user=request.user, is_active=True).first()
            if not goal:
                return Response({"error": "Active nutrition goal not found."}, status=status.HTTP_404_NOT_FOUND)

        # Get or create daily progress for this date
        # Note: unique constraint is on (user, date), so we use get_or_create with those fields
        # For historical dates, only get (don't create new records)
        if is_historical_date:
            progress = DailyNutritionProgress.objects.filter(
                user=request.user,
                date=query_date
            ).first()
            if not progress:
                # No progress exists for this historical date
                if not goal:
                    return Response({"error": "No nutrition goal found for this date."}, status=status.HTTP_404_NOT_FOUND)
                # Return empty progress with the goal that was active on that date
                return Response({
                    "goal": NutritionGoalSerializer(goal).data,
                    "progress": {
                        "water_consumed_ml": 0,
                        "calories_consumed_kcal": 0,
                        "protein_consumed_g": 0,
                        "carbs_consumed_g": 0,
                        "fat_consumed_g": 0,
                    },
                    "remaining": {
                        "water_ml": float(goal.water_intake_goal_ml or 0),
                        "calories_kcal": float(goal.calories_goal_kcal or 0),
                        "protein_g": float(goal.protein_goal_g or 0),
                        "carbs_g": float(goal.carbs_goal_g or 0),
                        "fat_g": float(goal.fat_goal_g or 0),
                    },
                }, status=status.HTTP_200_OK)
            # Use the goal from the progress record for historical dates
            if progress.goal:
                goal = progress.goal
        else:
            # For today or future dates, get or create and handle goal changes
            try:
                with transaction.atomic():
                    progress, created = DailyNutritionProgress.objects.get_or_create(
                        user=request.user,
                        date=query_date,
                        defaults={'goal': goal} if goal else {}
                    )
                    # Only reset progress for current/future dates when goal changes
                    if not created:
                        current_active_goal = NutritionGoal.objects.filter(user=request.user, is_active=True).first()
                        if current_active_goal and (progress.goal != current_active_goal or progress.goal is None or (progress.goal and not progress.goal.is_active)):
                            progress.goal = current_active_goal
                            progress.water_consumed_ml = 0
                            progress.calories_consumed_kcal = 0
                            progress.protein_consumed_g = 0
                            progress.carbs_consumed_g = 0
                            progress.fat_consumed_g = 0
                            progress.save()
            except IntegrityError:
                # Race condition: another request created the record between check and create
                progress = DailyNutritionProgress.objects.get(user=request.user, date=query_date)
                # Only reset for current/future dates
                current_active_goal = NutritionGoal.objects.filter(user=request.user, is_active=True).first()
                if current_active_goal and (progress.goal != current_active_goal or progress.goal is None or (progress.goal and not progress.goal.is_active)):
                    progress.goal = current_active_goal
                    progress.water_consumed_ml = 0
                    progress.calories_consumed_kcal = 0
                    progress.protein_consumed_g = 0
                    progress.carbs_consumed_g = 0
                    progress.fat_consumed_g = 0
                    progress.save()
        
        # If no goal found, return error
        if not goal:
            return Response({"error": "No nutrition goal found for this date."}, status=status.HTTP_404_NOT_FOUND)

        remaining = {
            "water_ml": max(0, float(goal.water_intake_goal_ml or 0) - progress.water_consumed_ml),
            "calories_kcal": max(0, float(goal.calories_goal_kcal or 0) - progress.calories_consumed_kcal),
            "protein_g": max(0, float(goal.protein_goal_g or 0) - progress.protein_consumed_g),
            "carbs_g": max(0, float(goal.carbs_goal_g or 0) - progress.carbs_consumed_g),
            "fat_g": max(0, float(goal.fat_goal_g or 0) - progress.fat_consumed_g),
        }

        return Response({
            "goal": {
                "water_intake_goal_ml": goal.water_intake_goal_ml,
                "calories_goal_kcal": goal.calories_goal_kcal,
                "protein_goal_g": goal.protein_goal_g,
                "carbs_goal_g": goal.carbs_goal_g,
                "fat_goal_g": goal.fat_goal_g,
                "base_water_intake_goal_ml": goal.base_water_intake_goal_ml,
            },
            "progress": {
                "water_consumed_ml": progress.water_consumed_ml,
                "calories_consumed_kcal": progress.calories_consumed_kcal,
                "protein_consumed_g": progress.protein_consumed_g,
                "carbs_consumed_g": progress.carbs_consumed_g,
                "fat_consumed_g": progress.fat_consumed_g,
            },
            "remaining": remaining,
        }, status=status.HTTP_200_OK)

    def post(self, request):
        """
        Log a drink (frontend sends OZ) and update DailyNutritionProgress
        Request:
        {
            "drink_name": "coffee",
            "drink_take_ml": 8,     # frontend sends OZ
            "date_time": "2025-10-23"
        }
        """
        drink_name = request.data.get("drink_name")
        drink_in_oz = request.data.get("drink_take_ml")
        date_time = request.data.get("date_time")

        if not all([drink_name, drink_in_oz, date_time]):
            return Response(
                {"error": "Please provide 'drink_name', 'drink_take_ml', and 'date_time'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            drink_in_oz = float(drink_in_oz)
            log_date = datetime.strptime(date_time, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date or drink value"}, status=status.HTTP_400_BAD_REQUEST)

        # Get active goal
        goal = NutritionGoal.objects.filter(user=request.user, is_active=True).first()
        if not goal:
            return Response({"error": "Active nutrition goal not found."}, status=status.HTTP_404_NOT_FOUND)

        # Get or create daily progress for this goal
        try:
            with transaction.atomic():
                progress, created = DailyNutritionProgress.objects.get_or_create(
                    user=request.user,
                    date=log_date,
                    defaults={'goal': goal}
                )
        except IntegrityError:
            # Someone else inserted the record; fetch it
            progress = DailyNutritionProgress.objects.get(user=request.user, date=log_date)

        # Normalize drink name
        normalized_name = re.sub(r'\b\d+\b', '', drink_name).strip().lower()
        match = re.search(r'\b(\d+)\b', drink_name)
        servings = int(match.group(1)) if match else 1

        # ----------------------------
        # CASE 1: Water
        # ----------------------------
        if normalized_name == "water":
            progress.water_consumed_ml += drink_in_oz
            progress.save()

            drink = DrinkNutrients.objects.create(
                user=request.user,
                date=log_date,
                name="Water",
                drink_take_ml=str(drink_in_oz),
                serving_qty=servings,
                serving_unit="oz",
                serving_weight_grams=drink_in_oz * ML_TO_OZ,
                water_grams=drink_in_oz * ML_TO_OZ,
                water_fraction=100.0,
                calories=0,
                sodium=0,
                potassium=0,
                fat=0,
            )

            serializer = DrinkSerializer(drink)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        # ----------------------------
        # CASE 2: Existing drink
        # ----------------------------
        drink = DrinkNutrients.objects.filter(
            user=request.user,
            name__iexact=normalized_name,
            date=log_date
        ).first()
        if drink:
            drink.serving_qty = servings
            water_value_oz = float(drink.water_grams) * servings / ML_TO_OZ
            progress.water_consumed_ml += water_value_oz
            progress.save()
            drink.save()
            serializer = DrinkSerializer(drink)
            return Response(serializer.data, status=status.HTTP_200_OK)

        # ----------------------------
        # CASE 3: New drink via Nutritionix
        # ----------------------------
        nutrition = NutritionXService()
        response = nutrition.nutrients(normalized_name)
        data = response.json()
        if "foods" not in data or not data["foods"]:
            return Response({"error": f"No nutrition data found for '{drink_name}'."}, status=status.HTTP_404_NOT_FOUND)

        total_water_ml = 0
        total_serving_weight = 0
        total_calories = 0
        total_sodium = 0
        total_potassium = 0
        total_fat = 0
        total_serving_unit = ""
        total_serving_qty = 0

        for food in data["foods"]:
            water_ml = next((item["value"] for item in food["full_nutrients"] if item["attr_id"] == 255), 0)
            total_water_ml += water_ml
            total_serving_weight += food.get("serving_weight_grams", 0)
            total_calories += food.get("nf_calories", 0)
            total_sodium += food.get("nf_sodium", 0)
            total_potassium += food.get("nf_potassium", 0)
            total_fat += food.get("nf_total_fat", 0)
            total_serving_unit += f"{food['serving_unit']} "
            total_serving_qty += food["serving_qty"]

        water_value_oz = (total_water_ml * servings) / ML_TO_OZ

        progress.water_consumed_ml += water_value_oz
        progress.calories_consumed_kcal += total_calories
        # Optionally: increment protein, carbs, fat if fetched
        progress.save()

        drink = DrinkNutrients.objects.create(
            user=request.user,
            date=log_date,
            name=normalized_name,
            drink_take_ml=str(drink_in_oz),
            serving_qty=total_serving_qty,
            serving_unit=total_serving_unit,
            serving_weight_grams=total_serving_weight,
            water_grams=total_water_ml,
            water_fraction=(total_water_ml / total_serving_weight * 100) if total_serving_weight else 0,
            calories=total_calories,
            sodium=total_sodium,
            potassium=total_potassium,
            fat=total_fat,
        )

        serializer = DrinkSerializer(drink)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
 
 
class NutritionGoalBulkCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    """
    POST: Create goals for multiple users
    GET: Retrieve all users with their goals
    """
    def post(self, request):
        from apps.users.models import UserRole
        if request.user.role == UserRole.CLIENT:
            return Response({"error": "Only staff users can create goals for users."},
                            status=status.HTTP_403_FORBIDDEN)
        
        # The structure is now flat (start_date, end_date, macros), not nested in "goals"
        serializer = BulkNutritionGoalSerializer(data=request.data)
        
        if serializer.is_valid():
            created_objs = serializer.save()
            
            # We return the list of all created/updated objects using the standard serializer
            return Response(
                {"created": NutritionGoalSerializer(created_objs, many=True).data},
                status=status.HTTP_201_CREATED
            )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request):
        user_id = request.query_params.get("user_id")
        history = request.query_params.get("history", "false").lower() == "true"

        if not user_id:
            return Response({"error": "user_id is required"}, status=400)

        qs = NutritionGoal.objects.filter(user_id=user_id)
        if not history:
            qs = qs.filter(is_active=True)

        serializer = NutritionGoalSerializer(qs, many=True)
        return Response(serializer.data)
  

class DailyProgressAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        date_str = request.query_params.get("date")
        if not date_str:
            return Response({"error": "date parameter is required"}, status=400)

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format"}, status=400)

        progress = DailyNutritionProgress.objects.filter(user=user, date=target_date).first()
        if not progress:
            return Response({"message": "No progress found for this date."}, status=404)

        goal = NutritionGoal.objects.filter(user=user, created_at__lte=target_date).order_by("-created_at").first()

        data = {
            "date": target_date,
            "goal": NutritionGoalSerializer(goal).data if goal else None,
            "progress": {
                "water_consumed_ml": progress.water_consumed_ml,
                "calories_consumed_kcal": progress.calories_consumed_kcal,
                "protein_consumed_g": progress.protein_consumed_g,
                "carbs_consumed_g": progress.carbs_consumed_g,
                "fat_consumed_g": progress.fat_consumed_g,
            }
        }
        return Response(data)



class CustomBeverageAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        beverages = CustomBeverage.objects.filter(user=request.user).order_by('-created_at')
        serializer = CustomBeverageSerializer(beverages, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
  
        serializer = CustomBeverageSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        beverage = serializer.save(user=request.user)

        image_file = request.FILES.get("image")
        if image_file:
            upload_image_to_s3(image_file, beverage)

        return Response(CustomBeverageSerializer(beverage).data, status=status.HTTP_201_CREATED)

    def put(self, request, pk):
        beverage = get_object_or_404(CustomBeverage, pk=pk, user=request.user)
        serializer = CustomBeverageSerializer(beverage, data=request.data, partial=True)
        old_image = beverage.image

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        beverage = serializer.save()

        image_file = request.FILES.get("image")
        name = request.data.get("name")
        type = request.data.get("type")
        if name:
            beverage.name = name
            beverage.save(update_fields=["name"])
        if type:
            beverage.type = type
            beverage.save(update_fields=["type"])
                
        if image_file:
            # Delete the old image from S3
            if beverage.image:
                delete_s3_file_threaded(old_image)

            file_url = upload_image_to_s3(image_file, beverage)
            beverage.image = file_url
            beverage.save(update_fields=["image"])

        return Response(CustomBeverageSerializer(beverage).data, status=status.HTTP_200_OK)

    def delete(self, request, pk):
        beverage = get_object_or_404(CustomBeverage, pk=pk, user=request.user)
        file_url = beverage.image
        beverage.delete()

        if file_url:
            delete_s3_file_threaded(file_url)

        return Response({"message": "Beverage deleted successfully."}, status=status.HTTP_204_NO_CONTENT)


class UserDrinkHistoryAPIView(APIView):
    permission_classes = [IsAuthenticated, IsClientUser]

    def get(self, request):
        """
        Returns all drinks consumed by the user with nutrients.
        Filters:
            ?date=YYYY-MM-DD
            ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD
        """

        user = request.user
        date_param = request.query_params.get("date")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        drinks = DrinkNutrients.objects.filter(user=user).order_by("-date")

        # Single date filter
        if date_param:
            try:
                query_date = datetime.strptime(date_param, "%Y-%m-%d").date()
                drinks = drinks.filter(date=query_date)
            except ValueError:
                return Response({"error": "Invalid date format."},
                                status=status.HTTP_400_BAD_REQUEST)

        # Date range filter
        if start_date and end_date:
            try:
                start = datetime.strptime(start_date, "%Y-%m-%d").date()
                end = datetime.strptime(end_date, "%Y-%m-%d").date()
                drinks = drinks.filter(date__range=[start, end])
            except ValueError:
                return Response({"error": "Invalid date range format."},
                                status=status.HTTP_400_BAD_REQUEST)

        serializer = DrinkSerializer(drinks, many=True)

        # Optional: Compute totals per response
        totals = {
            "total_water_oz": self.sum_field(drinks, "water_grams") / 29.5735,
            "total_calories": self.sum_field(drinks, "calories"),
            "total_fat": self.sum_field(drinks, "fat"),
            "total_sodium": self.sum_field(drinks, "sodium"),
            "total_potassium": self.sum_field(drinks, "potassium")
        }

        return Response({
            "totals": totals,
            "drinks": serializer.data
        }, status=status.HTTP_200_OK)

    @staticmethod
    def sum_field(qs, field):
        total = 0
        for item in qs:
            value = getattr(item, field, 0)
            try:
                total += float(value)
            except:
                pass
        return total


class ClientMacroHistoryListView(generics.ListAPIView):
    """
    API to view Current and Historical MACRO Limits of all Client Users.
    Optimized for zero N+1 queries.
    """
    serializer_class = ClientMacroHistorySerializer
    permission_classes = [IsStaffUser]
    
    
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    search_fields = ['email']

    def get_queryset(self):
        all_goals_prefetch = Prefetch(
            'nutrition_goals',
            queryset=NutritionGoal.objects.all().order_by('-created_at'),
            to_attr='all_goals'
        )

        # 2. Main Queryset
        return User.objects.filter(
            user_type=User.UserType.CLIENT,
            deactivated=False
        ).prefetch_related(
            all_goals_prefetch
        )
    