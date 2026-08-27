import io
from datetime import date
from zoneinfo import ZoneInfo
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework import viewsets
from .permissions import IsOwnerOrStaffReadOnly
from django.core.files.base import ContentFile
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from .models import FoodSuggestion, StaffRecipe, StaffRecipeItem, UserMedication, UserRecipeItem, UserNutritionGoal, CustomMeal, \
    MealItem, LoggedMeal, LoggedMealItem, CustomFood, FavoriteStaffRecipes
from .pagination import StaffRecipePagination, CustomPagination
from .serializers import ClientFoodLogSerializer, FoodSuggestionSerializer, LoggedMealGetSerializer, StaffRecipeItemUpdateSerializer, StaffRecipeSerializer, StaffRecipeItemSerializer, \
    StaffRecipeListSerializer, StaffRecipeUpdateSerializer, UserMedicationSerializer, UserRecipeItemSerializer, BulkNutritionGoalSerializer, NutritionGoalSerializer, \
    CustomMealSerializer, NutritionXGoalSerializer, LoggedMealSerializer, UserFoodSerializer, \
    FavoriteStaffRecipeSerializer
from apps.users.models import User, UserRole
from ..nutritionX.nutritionx_service import NutritionXService
from django.db import transaction
from django.utils import timezone
import datetime
import sys
from rest_framework import generics, permissions, filters
from rest_framework.permissions import BasePermission, IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend

# Fallback S3 upload helper for recipe images
def food_loger_s3(file_obj, obj):
    import os
    import uuid
    from django.core.files.storage import FileSystemStorage
    from django.conf import settings
    
    subfolder = 'recipe_images'
    fs = FileSystemStorage(
        location=os.path.join(settings.MEDIA_ROOT, subfolder),
        base_url=f"/media/{subfolder}/"
    )
    ext = os.path.splitext(file_obj.name)[1]
    filename = f"{uuid.uuid4()}{ext}"
    saved_filename = fs.save(filename, file_obj)
    
    url = fs.url(saved_filename)
    if hasattr(obj, 'image'):
        obj.image = url
        obj.save()
    return url

# Permission Classes
def is_staff_user(user):
    return user.is_authenticated and user.role in [
        UserRole.TRAINER, UserRole.GYM_MANAGER, UserRole.GYM_OWNER, UserRole.PLATFORM_ADMIN
    ]

class IsClientUser(BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == UserRole.CLIENT

class IsStaffUser(BasePermission):
    def has_permission(self, request, view):
        return is_staff_user(request.user)

# --- Staff Suggestion ---

class NutritionGoalBulkCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    """
    POST: Create goals for multiple users
    GET: Retrieve all users with their goals
    """

    def post(self, request):
        if not is_staff_user(request.user):
            return Response({"error": "Only staff users can create goals for users."},
                            status=status.HTTP_403_FORBIDDEN)

        serializer = BulkNutritionGoalSerializer(data=request.data)
        if serializer.is_valid():
            created_objs = serializer.save()
            return Response(
                {"created": NutritionGoalSerializer(created_objs, many=True).data},
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request):

        user_id = request.query_params.get("user_id")
        date_str = request.query_params.get("date")

        goals = UserNutritionGoal.objects.filter(user_id=user_id)
        if not user_id:
            return Response({"error": "user_id query param is required"}, status=status.HTTP_400_BAD_REQUEST)
        if date_str:
            from django.utils.dateparse import parse_date, parse_datetime
            date = parse_date(str(date_str))
            if not date:
                parsed_dt = parse_datetime(str(date_str))
                if parsed_dt:
                    date = parsed_dt.date()
            if date:
                goals = goals.filter(date=date)

        serializer = NutritionXGoalSerializer(goals, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request):
        """
        Bulk update multiple goals
        """
        if not is_staff_user(request.user):
            return Response(
                {"error": "Only staff users can update goals."},
                status=status.HTTP_403_FORBIDDEN
            )

        data = request.data
        updated_goals = []
        errors = []

        for goal_data in data:
            goal_id = goal_data.get("id")
            if not goal_id:
                errors.append({"error": "Goal ID missing", "data": goal_data})
                continue

            try:
                goal = UserNutritionGoal.objects.get(id=goal_id)
            except UserNutritionGoal.DoesNotExist:
                errors.append({"error": f"Goal with id {goal_id} not found"})
                continue

            serializer = NutritionGoalSerializer(goal, data=goal_data, partial=True)
            if serializer.is_valid():
                serializer.save()
                updated_goals.append(serializer.data)
            else:
                errors.append(serializer.errors)

        response_data = {"updated": updated_goals}
        if errors:
            response_data["errors"] = errors

        return Response(response_data, status=status.HTTP_200_OK)

class FoodSuggestionListCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StaffRecipePagination


    def get(self, request):

        suggestions = FoodSuggestion.objects.all()
        paginator = self.pagination_class()
        paginated_qs = paginator.paginate_queryset(suggestions, request)

        serializer = FoodSuggestionSerializer(paginated_qs, many=True)
        return paginator.get_paginated_response(serializer.data)


    def post(self, request):
        if not is_staff_user(request.user):
            return Response({'error': 'Only staff can create suggestions'}, status=403)
        serializer = FoodSuggestionSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(staff=request.user)
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

    def delete(self, request):
        if not is_staff_user(request.user):
            return Response({'error': 'Only staff can delete suggestions'}, status=403)
        food_suggestion_id = request.data.get('food_suggestion_id')
        try:
            FoodSuggestion.objects.get(id=food_suggestion_id).delete()
            return Response(status=status.HTTP_200_OK, data={'message': 'Suggestion deleted'})
        except FoodSuggestion.DoesNotExist:
            return Response({'error': 'Suggestion not found'}, status=status.HTTP_404_NOT_FOUND)

class StaffRecipeListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = StaffRecipePagination
    parser_classes = [MultiPartParser, FormParser, JSONParser]



    def get(self, request):
        recipes = StaffRecipe.objects.all().order_by('-created_at')
        if not recipes.exists():
            from django.utils import timezone
            defaults = [
                {"id": 1, "category": "Breakfast", "created_at": timezone.now().date()},
                {"id": 2, "category": "Lunch", "created_at": timezone.now().date()},
                {"id": 3, "category": "Dinner", "created_at": timezone.now().date()},
                {"id": 4, "category": "Snack", "created_at": timezone.now().date()},
                {"id": 5, "category": "Pre Workout", "created_at": timezone.now().date()},
                {"id": 6, "category": "Post Workout", "created_at": timezone.now().date()},
            ]
            return Response(defaults)
        serializer = StaffRecipeListSerializer(recipes, many=True)
        return Response(serializer.data)



    def post(self, request):
        if not is_staff_user(request.user):
             return Response({"detail": "Only staff can create recipes"}, status=403)
        serializer = StaffRecipeSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            recipe = serializer.save()
            created_item = getattr(recipe, '_created_item', None)
            if created_item:
                file_obj = request.FILES.get("image")
                if file_obj:
                    file_copy = io.BytesIO(file_obj.read())
                    file_copy.name = file_obj.name
                    food_loger_s3(file_copy, created_item)
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

    def delete(self, request):
        if not is_staff_user(request.user):
            return Response({"detail": "Only staff can delete recipes"}, status=status.HTTP_403_FORBIDDEN)

        recipe_id = request.data.get('category_id')
        item_id = request.data.get('item_id')
        is_recipe = request.data.get('is_recipe', None)

        if not recipe_id:
            return Response({"detail": "recipe_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            if is_recipe is not None and is_recipe != False:
                recipe = StaffRecipe.objects.get(id=recipe_id)
                recipe.delete()
                return Response({"message": "Recipe deleted successfully"}, status=status.HTTP_200_OK)

            if not item_id:
                return Response({"detail": "item_id is required when deleting an item"}, status=status.HTTP_400_BAD_REQUEST)

            item = StaffRecipeItem.objects.get(id=item_id, recipe__id=recipe_id)
            item.delete()
            return Response({"message": "Item deleted successfully"}, status=status.HTTP_200_OK)

        except StaffRecipe.DoesNotExist:
            return Response({"detail": "Recipe not found"}, status=status.HTTP_404_NOT_FOUND)
        except StaffRecipeItem.DoesNotExist:
            return Response({"detail": "Item not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class StaffRecipeItemListAPIView(APIView):
    permission_classes = [IsAuthenticated]
    pagination_class = StaffRecipePagination

    # def get(self, request, recipe_id):
    #     recipe = get_object_or_404(StaffRecipe, id=recipe_id)
    #     items = recipe.items.all().order_by('-created_at')
    #
    #     paginator = self.pagination_class()
    #     paginated_items = paginator.paginate_queryset(items, request)
    #     serializer = StaffRecipeItemSerializer(paginated_items, many=True)
    #
    #     return paginator.get_paginated_response(serializer.data)

    # def get(self, request):
    #     paginator = self.pagination_class()
    #
    #     items = StaffRecipeItem.objects.select_related('recipe').order_by('-created_at')
    #
    #     paginated_items = paginator.paginate_queryset(items, request)
    #     grouped_data = defaultdict(list)
    #     for item in paginated_items:
    #         category = item.recipe.category or "Uncategorized"
    #         serializer = StaffRecipeItemSerializer(item)
    #         grouped_data[category].append(serializer.data)
    #
    #     return paginator.get_paginated_response(grouped_data)

    def get(self, request):
        recipes = StaffRecipe.objects.prefetch_related(
            Prefetch('items', queryset=StaffRecipeItem.objects.order_by('-created_at'))
        ).all()

        grouped_data = []

        for recipe in recipes:
            items = recipe.items.all()
            serializer = StaffRecipeItemSerializer(items, many=True)
            grouped_data.append({
                "category_id": recipe.id,
                "category_name": recipe.category or "Uncategorized",
                "items": serializer.data
            })

        return Response(grouped_data)



# ----- User recipe




class AddFoodToMealView(APIView):
    permission_classes = [IsAuthenticated]

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


class UserRecipeItemListCreateAPIView(APIView):
        permission_classes = [IsAuthenticated, IsClientUser]
        pagination_class = CustomPagination

        def get(self, request):
            queryset = UserRecipeItem.objects.filter(user=request.user).order_by('-created_at')

            paginator = self.pagination_class()
            paginated_qs = paginator.paginate_queryset(queryset, request)

            serializer = UserRecipeItemSerializer(paginated_qs, many=True)
            return paginator.get_paginated_response(serializer.data)

        def post(self, request):
            serializer = UserRecipeItemSerializer(
                data=request.data, context={"request": request}
            )
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        def patch(self, request):
            """
            Ability to edit a custom recipe item for an individual user.
            """
            recipe_id = request.query_params.get("recipe_id")
            if not recipe_id:
                return Response({"error": "recipe_id is required."}, status=status.HTTP_400_BAD_REQUEST)

            # Security: Filter by both ID and the logged-in user
            recipe = get_object_or_404(UserRecipeItem, id=recipe_id, user=request.user)
            
            serializer = UserRecipeItemSerializer(recipe, data=request.data, partial=True, context={"request": request})
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data, status=status.HTTP_200_OK)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        def delete(self, request):
            recipe_id = request.query_params.get("recipe_id")
            if not recipe_id:
                return Response(
                    {"error": "recipe id is required."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            recipe = get_object_or_404(UserRecipeItem, id=recipe_id, user=request.user)
            recipe.delete()

            return Response(
                {"detail": f"recipe (ID: {recipe_id}) deleted successfully."},
                status=status.HTTP_200_OK
            )

class UserFavoriteStaffRecipeAPIView(APIView):
    permission_classes = [IsAuthenticated, IsClientUser]

    def get(self, request):
        """
        Get all favorite recipes for the logged-in user
        """
        favorites = FavoriteStaffRecipes.objects.filter(user=request.user, is_favorite=True)
        serializer = FavoriteStaffRecipeSerializer(favorites, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        """
        favorite/unfavorite
        """
        user = request.user
        recipe_id = request.data.get("staff_recipe_id")

        if not recipe_id:
            return Response({"detail": "staff_recipe_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        recipe = get_object_or_404(StaffRecipeItem, id=recipe_id)

        favorite, created = FavoriteStaffRecipes.objects.get_or_create(
            user=user,
            staff_recipe=recipe,
            defaults={"is_favorite": True}
        )

        if not created:
            favorite.is_favorite = not favorite.is_favorite
            favorite.save()

        message = "Added to favorites" if favorite.is_favorite else "Removed from favorites"
        return Response({"detail": message, "is_favorite": favorite.is_favorite}, status=status.HTTP_200_OK)

class CustomMealView(APIView):
    permission_classes = [IsAuthenticated, IsClientUser]

    def get(self, request):
        meals = CustomMeal.objects.filter(user=request.user).prefetch_related('items')
        serializer = CustomMealSerializer(meals, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = CustomMealSerializer(data=request.data, context={"request": request})
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

    def put(self, request, meal_id):
        """
        Update meal details and sync ingredients.
        """
        meal = get_object_or_404(CustomMeal, id=meal_id, user=request.user)

        data = request.data.copy()
        items_data = data.pop("items", None) # List of meal items (ingredients)
        
        # 1. Update Meal name/directions
        serializer = CustomMealSerializer(meal, data=data, partial=True, context={"request": request})
        if serializer.is_valid():
            serializer.save()

            # 2. Update Items if provided
            if items_data is not None:
                # Simple Sync Strategy: Remove old items and recreate new ones 
                # or match by ID for a more complex update.
                meal.items.all().delete()
                for item_data in items_data:
                    MealItem.objects.create(meal=meal, **item_data)

            return Response(CustomMealSerializer(meal).data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def delete(self, request):
        meal_id = request.query_params.get("meal_id")
        item_id = request.query_params.get("item_id")

        if not meal_id:
            return Response({"detail": "meal_id query param is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Get the meal; will raise 404 if not found
        meal = get_object_or_404(CustomMeal, id=meal_id, user=request.user)

        if item_id:
            # Delete specific item
            item = meal.items.filter(id=item_id).first()
            if not item:
                return Response({"detail": "Item not found"}, status=status.HTTP_404_NOT_FOUND)
            item.delete()
            return Response({"detail": "Item deleted successfully"}, status=status.HTTP_200_OK)
        else:
            # Delete the entire meal
            meal.delete()
            return Response({"detail": "Meal deleted successfully"}, status=status.HTTP_200_OK)

class CustomFoodApiView(APIView):
    permission_classes = [IsAuthenticated, IsClientUser]

    def post(self, request):
        serializer = UserFoodSerializer(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def get(self, request):
        user = request.user
        today = request.query_params.get("date")
        is_custom_food = request.query_params.get("is_custom_food", None)

        food = CustomFood.objects.filter(user=user)

        if is_custom_food is not None:
            is_custom_bool = str(is_custom_food).lower() in ['true', '1']
            food = food.filter(is_custom_food=is_custom_bool)

        if today:
            food = food.filter(date=today)

        serializer = UserFoodSerializer(food, many=True)
        return Response(
            {"date": str(today), "food": serializer.data},
            status=status.HTTP_200_OK
        )

    def patch(self, request):
        """
        Ability to edit a custom food for an individual user.
        """
        user = request.user
        food_id = request.query_params.get("food_id")

        if not food_id:
            return Response({"error": "food_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Ensure the food belongs to the requesting user
        food = get_object_or_404(CustomFood, id=food_id, user=user)
        
        serializer = UserFoodSerializer(food, data=request.data, partial=True, context={"request": request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)   

    def delete(self, request):
        user = request.user
        food_id = request.query_params.get("food_id")

        if not food_id:
            return Response(
                {"error": "food_id is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        food = get_object_or_404(CustomFood, id=food_id, user=user)
        food.delete()

        return Response(
            {"detail": f"Food (ID: {food_id}) deleted successfully."},
            status=status.HTTP_200_OK
        )

class GetAllFoodApiView(APIView):
    permission_classes = [AllowAny]
    pagination_class = CustomPagination

    def get(self, request):
        suggestions = CustomFood.objects.filter(is_custom_food=True)
        paginator = self.pagination_class()
        paginated_qs = paginator.paginate_queryset(suggestions, request)

        serializer = UserFoodSerializer(paginated_qs, many=True)
        return paginator.get_paginated_response(serializer.data)

class LogFoodAPIView(APIView):
    permission_classes = [IsAuthenticated, IsClientUser]

    def post(self, request):
        user = request.user
        meal_type = request.data.get("meal_type")
        serving = float(request.data.get("serving", 1))
         
        date_input = request.data.get("date") or request.query_params.get("date")
        today = None
        if date_input:
            from django.utils.dateparse import parse_date, parse_datetime
            today = parse_date(str(date_input))
            if not today:
                parsed_dt = parse_datetime(str(date_input))
                if parsed_dt:
                    today = parsed_dt.date()
        
        if not today:
            today = timezone.localdate()

        # 1. Create/Get logged meal for today
        logged_meal, _ = LoggedMeal.objects.get_or_create(
            user=user,
            meal_type=meal_type,
            created_at=today
        )

        # Running totals
        total_calories = total_protein = total_carbs = total_fats = 0

        # Map request keys → model
        source_map = {
            "staff_recipe_item": StaffRecipeItem,
            "user_recipe_item": UserRecipeItem,
            "meal": MealItem,
        }

        # ---------- Helper to log item ----------
        def log_item(item, extra_kwargs):
            nonlocal total_calories, total_protein, total_carbs, total_fats

            # Multiply nutrients by servings
            calories = item.calories * serving
            protein = item.protein * serving
            carbs = item.carbs * serving
            fats = item.fats * serving

            # Create LoggedMealItem
            LoggedMealItem.objects.create(
                logged_meal=logged_meal,
                name=item.name,
                calories=calories,
                protein=protein,
                carbs=carbs,
                fats=fats,
                logged_serving_qty=serving,
                **extra_kwargs
            )

            # Update totals
            total_calories += calories
            total_protein += protein
            total_carbs += carbs
            total_fats += fats

        # ---------- Process mapped sources ----------
        for key, model in source_map.items():
            ids = request.data.get(key)
            if not ids:
                continue

            items = model.objects.filter(id__in=ids)
            if not items:
                return Response({"detail": "Item not found"}, status=404)

            for item in items:
                log_item(item, {key: item})

        # ---------- Food Suggestion ----------
        suggestion_id = request.data.get("suggestion")
        if suggestion_id:
            suggestion = get_object_or_404(FoodSuggestion, id=suggestion_id)
            log_item(suggestion, {"suggestion": suggestion})

        # ---------- Custom food payload ----------
        food_item_data = request.data.get("food_item")
        food_id = request.data.get("food")

        if food_item_data:
            food_item_data["user"] = user
            custom_food = CustomFood.objects.create(**food_item_data)
            log_item(custom_food, {"food": custom_food})

        elif food_id:
            food_item = get_object_or_404(CustomFood, id=food_id)
            log_item(food_item, {"food": food_item})

        # ---------- Update LoggedMeal totals ----------
        logged_meal.total_calories += total_calories
        logged_meal.total_protein += total_protein
        logged_meal.total_carbs += total_carbs
        logged_meal.total_fats += total_fats
        logged_meal.save()

        # ---------- Update nutrition goal ----------
        goal, _ = UserNutritionGoal.objects.get_or_create(user=user, date=today)

        goal.daily_calories -= total_calories
        goal.daily_protein -= total_protein
        goal.daily_carbs -= total_carbs
        goal.daily_fat -= total_fats
        goal.save()

        return Response({
            "message": f"Logged successfully to {meal_type}",
            "meal_totals": {
                "calories": logged_meal.total_calories,
                "protein": logged_meal.total_protein,
                "carbs": logged_meal.total_carbs,
                "fats": logged_meal.total_fats,
            },
            "remaining_goals": {
                "calories": goal.daily_calories,
                "protein": goal.daily_protein,
                "carbs": goal.daily_carbs,
                "fat": goal.daily_fat,
            }
        }, status=201)

    def get(self, request):
        user = request.user
        today_input = request.query_params.get("date")
        today = None
        if today_input:
            from django.utils.dateparse import parse_date, parse_datetime
            today = parse_date(str(today_input))
            if not today:
                parsed_dt = parse_datetime(str(today_input))
                if parsed_dt:
                    today = parsed_dt.date()
        
        if not today:
            today = timezone.localdate()

        meals = (
            LoggedMeal.objects.filter(user=user, created_at=today)
            .prefetch_related(
                "items__staff_recipe_item",
                "items__user_recipe_item",
                "items__meal",
                "items__suggestion",
            )
            .order_by("meal_type")
        )

        serializer = LoggedMealSerializer(meals, many=True)
        return Response({
            "date": str(today),
            "meals": serializer.data
        }, status=status.HTTP_200_OK)

    def delete(self, request):
        user = request.user
        item_id = request.query_params.get("logged_meal_id")

        if not item_id:
            return Response({"detail": "item_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        logged_item = get_object_or_404(LoggedMealItem, id=item_id, logged_meal__user=user)
        logged_meal = logged_item.logged_meal

        # Store values before deletion
        calories = logged_item.calories
        protein = logged_item.protein
        carbs = logged_item.carbs
        fats = logged_item.fats

        # Delete item
        logged_item.delete()

        # Update meal totals
        logged_meal.total_calories -= calories
        logged_meal.total_protein -= protein
        logged_meal.total_carbs -= carbs
        logged_meal.total_fats -= fats
        logged_meal.save()

        # Update user goal
        goal, _ = UserNutritionGoal.objects.get_or_create(user=user, date=logged_meal.created_at)
        goal.daily_calories += calories
        goal.daily_protein += protein
        goal.daily_carbs += carbs
        goal.daily_fat += fats
        goal.save()

        # Optional: if no more items, delete empty meal
        if not logged_meal.items.exists():
            logged_meal.delete()

        return Response({
            "detail": "Item deleted successfully",
            "updated_meal_totals": {
                "calories": logged_meal.total_calories,
                "protein": logged_meal.total_protein,
                "carbs": logged_meal.total_carbs,
                "fats": logged_meal.total_fats,
            },
            "remaining_goals": {
                "calories": goal.daily_calories,
                "protein": goal.daily_protein,
                "carbs": goal.daily_carbs,
                "fat": goal.daily_fat,
            }
        }, status=status.HTTP_200_OK)

class LoggedMealDatesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        dates = (
            LoggedMeal.objects
            .filter(user=request.user)
            .values_list("created_at", flat=True)
            .distinct()
            .order_by("created_at")
        )

        # Convert date objects to ISO strings
        date_strings = [d.isoformat() for d in dates]

        return Response({"dates": date_strings})


class StaffRecipeItemUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def put(self, request, *args, **kwargs):
        return self.update_item(request)

    def patch(self, request, *args, **kwargs):
        return self.update_item(request, partial=True)

    def update_item(self, request, partial=False):
        if not is_staff_user(request.user):
            return Response({"detail": "Only staff can update items"}, status=403)

        recipe_id = request.query_params.get("recipe_id")
        item_id = request.query_params.get("item_id")

        if not recipe_id or not item_id:
            return Response({"detail": "recipe_id and item_id are required"}, status=400)

        try:
            item = StaffRecipeItem.objects.get(id=item_id, recipe_id=recipe_id)
        except StaffRecipeItem.DoesNotExist:
            return Response({"detail": "Item not found"}, status=404)

        serializer = StaffRecipeItemUpdateSerializer(item, data=request.data, context={"request": request}, partial=partial)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=200)

        return Response(serializer.errors, status=400)
    

class StaffRecipeUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, recipe_id):
        if not is_staff_user(request.user):
            return Response({"detail": "Only staff can update recipes"}, status=403)

        try:
            recipe = StaffRecipe.objects.get(id=recipe_id)
        except StaffRecipe.DoesNotExist:
            return Response({"detail": "Recipe not found"}, status=404)

        serializer = StaffRecipeUpdateSerializer(recipe, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=200)

        return Response(serializer.errors, status=400)


class FoodLogGetApi(APIView):
    permission_classes = [IsAuthenticated, IsClientUser]

    def get(self, request):
        user = request.user
        today = request.query_params.get("date", date.today())

        meals = (
            LoggedMeal.objects.filter(user=user, created_at=today)
            .prefetch_related(
                "items__staff_recipe_item",
                "items__user_recipe_item",
                "items__meal__meal",  # <--- This allows item.meal.meal to work in the serializer
                "items__suggestion",
                "items__food",
            )
            .order_by("meal_type")
        )

        serializer = LoggedMealGetSerializer(meals, many=True)
        return Response({
            "date": str(today),
            "meals": serializer.data
        }, status=status.HTTP_200_OK)

    def delete(self, request, *args, **kwargs):
        user = request.user
        item_ids = request.data.get("item_ids", [])

        if not item_ids:
            return Response({"detail": "item_ids are required"}, status=status.HTTP_400_BAD_REQUEST)

        items_to_delete = LoggedMealItem.objects.filter(id__in=item_ids, logged_meal__user=user)

        if not items_to_delete.exists():
            return Response({"detail": "No matching items found"}, status=status.HTTP_404_NOT_FOUND)

        deleted_count = items_to_delete.count()

        # Use transaction to ensure consistency
        with transaction.atomic():
            meal_updates = {}
            goal_updates = {}

            # Prepare updates
            for item in items_to_delete:
                meal = item.logged_meal
                # Aggregate meal totals
                if meal.id not in meal_updates:
                    meal_updates[meal.id] = {
                        "meal": meal,
                        "calories": 0,
                        "protein": 0,
                        "carbs": 0,
                        "fats": 0
                    }
                meal_updates[meal.id]["calories"] += item.calories
                meal_updates[meal.id]["protein"] += item.protein
                meal_updates[meal.id]["carbs"] += item.carbs
                meal_updates[meal.id]["fats"] += item.fats

                # Aggregate user goal adjustments
                goal_key = (user.id, meal.created_at)
                if goal_key not in goal_updates:
                    goal, _ = UserNutritionGoal.objects.get_or_create(user=user, date=meal.created_at)
                    goal_updates[goal_key] = goal

            # Delete items
            items_to_delete.delete()

            # Update meal totals
            for update in meal_updates.values():
                meal = update["meal"]
                meal.total_calories = max(0, meal.total_calories - update["calories"])
                meal.total_protein = max(0, meal.total_protein - update["protein"])
                meal.total_carbs = max(0, meal.total_carbs - update["carbs"])
                meal.total_fats = max(0, meal.total_fats - update["fats"])
                meal.save()

                # Delete meal if empty
                if not meal.items.exists():
                    meal.delete()

            # Update user nutrition goals
            for goal in goal_updates.values():
                items_for_goal = [item for item in items_to_delete if item.logged_meal.created_at == goal.date]
                goal.daily_calories = max(0, goal.daily_calories - sum(i.calories for i in items_for_goal))
                goal.daily_protein = max(0, goal.daily_protein - sum(i.protein for i in items_for_goal))
                goal.daily_carbs = max(0, goal.daily_carbs - sum(i.carbs for i in items_for_goal))
                goal.daily_fat = max(0, goal.daily_fat - sum(i.fats for i in items_for_goal))
                goal.save()

        return Response({
            "deleted": deleted_count,
            "updated_meals": len(meal_updates),
            "updated_goals": len(goal_updates)
        }, status=status.HTTP_200_OK)




class UserMedicationViewSet(viewsets.ModelViewSet):
    serializer_class = UserMedicationSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrStaffReadOnly]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return UserMedication.objects.none()

        queryset = UserMedication.objects.all()
        if is_staff_user(self.request.user):
            client_id = self.request.query_params.get('client_id')
            if client_id:
                return queryset.filter(user_id=client_id)
            return queryset
        return queryset.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

class StaffClientFoodLogListView(generics.ListAPIView):
    """
    API for Staff to view and filter all client food logs.
    Optimized for zero N+1 queries.
    """
    serializer_class = ClientFoodLogSerializer
    permission_classes = [IsStaffUser]
    
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    

    filterset_fields = {
        'user__id': ['exact'],                   
        'meal_type': ['exact'],                  
        'created_at': ['exact', 'gte', 'lte'],   
        'total_calories': ['gte', 'lte'],        
    }
    
    search_fields = ['user__email', 'user__profile__first_name', 'items__name']
    
    ordering_fields = ['created_at', 'total_calories']
    ordering = ['-created_at']

    def get_queryset(self):
        return LoggedMeal.objects.select_related(
            'user', 
            'user__profile'
        ).prefetch_related(
            'items'
        )
    