"""
Default Nutrition Goals Service

Provides gender-based default nutrition macro and water intake goals for Male and Female users
across both `apps.nutritionX.models.NutritionGoal` and `apps.food_logger.models.UserNutritionGoal`.
"""
from datetime import date as date_cls
from django.utils import timezone


DEFAULT_NUTRITION_GOALS = {
    "male": {
        "calories_goal_kcal": 2500.0,
        "protein_goal_g": 160.0,
        "carbs_goal_g": 280.0,
        "fat_goal_g": 80.0,
        "water_intake_goal_ml": 3000.0,
        "base_water_intake_goal_ml": 3000.0,
    },
    "female": {
        "calories_goal_kcal": 2000.0,
        "protein_goal_g": 130.0,
        "carbs_goal_g": 220.0,
        "fat_goal_g": 65.0,
        "water_intake_goal_ml": 2200.0,
        "base_water_intake_goal_ml": 2200.0,
    },
}


def get_user_gender_key(user) -> str:
    """
    Resolves the normalized gender key ('male' or 'female') from a User or UserProfile.
    Defaults to 'female' if gender is female, or 'male' if male; falls back to 'female'
    when unset/other unless specified.
    """
    gender = ""
    try:
        if hasattr(user, "profile") and user.profile:
            gender = (getattr(user.profile, "gender", "") or "").strip().lower()
    except Exception:
        gender = ""

    if gender in ("male", "m"):
        return "male"
    if gender in ("female", "f"):
        return "female"
    return "female"


def get_default_nutrition_values_for_gender(gender_or_user=None) -> dict:
    """
    Returns a dictionary of default macro and water goals for a gender string ('male'/'female')
    or a User instance.
    """
    if isinstance(gender_or_user, str):
        key = gender_or_user.strip().lower()
        if key in ("male", "m"):
            key = "male"
        else:
            key = "female"
    elif gender_or_user is not None:
        key = get_user_gender_key(gender_or_user)
    else:
        key = "female"

    return dict(DEFAULT_NUTRITION_GOALS[key])


def is_goal_matching_any_default(nutrition_goal) -> bool:
    """
    Checks whether an existing NutritionGoal matches either the male or female default values,
    so we know if it is safe to auto-switch when a user updates their profile gender.
    """
    if not nutrition_goal:
        return False
    try:
        cal = float(nutrition_goal.calories_goal_kcal or 0)
        prot = float(nutrition_goal.protein_goal_g or 0)
        carb = float(nutrition_goal.carbs_goal_g or 0)
        fat = float(nutrition_goal.fat_goal_g or 0)
        for preset in DEFAULT_NUTRITION_GOALS.values():
            if (
                abs(cal - preset["calories_goal_kcal"]) < 0.01
                and abs(prot - preset["protein_goal_g"]) < 0.01
                and abs(carb - preset["carbs_goal_g"]) < 0.01
                and abs(fat - preset["fat_goal_g"]) < 0.01
            ):
                return True
    except (ValueError, TypeError):
        return False
    return False


def ensure_default_nutritionx_goal(user, update_if_default: bool = False):
    """
    Ensures the user has an active `apps.nutritionX.models.NutritionGoal`.
    If none exists, creates one with the user's gender-based defaults.
    If `update_if_default=True` and the user's only active goal is still an unmodified default preset,
    updates it to match the user's new gender.
    """
    if not user or not getattr(user, "pk", None):
        return None

    from apps.nutritionX.models import NutritionGoal
    from apps.core.tenants.context import get_current_tenant

    tenant = getattr(user, "tenant", None) or get_current_tenant()
    if not tenant:
        return None

    defaults = get_default_nutrition_values_for_gender(user)
    active_goal = NutritionGoal.all_objects.filter(user=user, is_active=True).order_by("-created_at").first()

    if active_goal:
        if update_if_default and is_goal_matching_any_default(active_goal):
            active_goal.calories_goal_kcal = str(int(defaults["calories_goal_kcal"]))
            active_goal.protein_goal_g = str(int(defaults["protein_goal_g"]))
            active_goal.carbs_goal_g = str(int(defaults["carbs_goal_g"]))
            active_goal.fat_goal_g = str(int(defaults["fat_goal_g"]))
            active_goal.water_intake_goal_ml = str(int(defaults["water_intake_goal_ml"]))
            active_goal.base_water_intake_goal_ml = str(int(defaults["base_water_intake_goal_ml"]))
            if not active_goal.tenant_id and tenant:
                active_goal.tenant = tenant
            active_goal.save(update_fields=[
                "calories_goal_kcal",
                "protein_goal_g",
                "carbs_goal_g",
                "fat_goal_g",
                "water_intake_goal_ml",
                "base_water_intake_goal_ml",
                "tenant",
                "updated_at",
            ])
        return active_goal

    return NutritionGoal.all_objects.create(
        user=user,
        tenant=tenant,
        calories_goal_kcal=str(int(defaults["calories_goal_kcal"])),
        protein_goal_g=str(int(defaults["protein_goal_g"])),
        carbs_goal_g=str(int(defaults["carbs_goal_g"])),
        fat_goal_g=str(int(defaults["fat_goal_g"])),
        water_intake_goal_ml=str(int(defaults["water_intake_goal_ml"])),
        base_water_intake_goal_ml=str(int(defaults["base_water_intake_goal_ml"])),
        is_active=True,
    )


def ensure_default_food_logger_goal(user, target_date=None, update_if_default: bool = False):
    """
    Ensures the user has a `apps.food_logger.models.UserNutritionGoal` for `target_date` (or today).
    Uses the user's active `NutritionGoal` if custom-configured, or falls back to gender-based defaults.
    """
    if not user or not getattr(user, "pk", None):
        return None

    from apps.food_logger.models import UserNutritionGoal

    if target_date is None:
        target_date = timezone.localdate()

    # Check if user has an active NutritionGoal in nutritionX first (which may have been set by trainer or default)
    nx_goal = ensure_default_nutritionx_goal(user, update_if_default=update_if_default)
    defaults = get_default_nutrition_values_for_gender(user)

    def _to_float(val, fallback):
        try:
            v = float(val)
            return v if v > 0 else fallback
        except (ValueError, TypeError):
            return fallback

    if nx_goal:
        cal_val = _to_float(nx_goal.calories_goal_kcal, defaults["calories_goal_kcal"])
        prot_val = _to_float(nx_goal.protein_goal_g, defaults["protein_goal_g"])
        carb_val = _to_float(nx_goal.carbs_goal_g, defaults["carbs_goal_g"])
        fat_val = _to_float(nx_goal.fat_goal_g, defaults["fat_goal_g"])
        water_val = _to_float(nx_goal.water_intake_goal_ml, defaults["water_intake_goal_ml"])
        base_val = _to_float(nx_goal.base_water_intake_goal_ml, defaults["base_water_intake_goal_ml"])
    else:
        cal_val = defaults["calories_goal_kcal"]
        prot_val = defaults["protein_goal_g"]
        carb_val = defaults["carbs_goal_g"]
        fat_val = defaults["fat_goal_g"]
        water_val = defaults["water_intake_goal_ml"]
        base_val = defaults["base_water_intake_goal_ml"]

    goal, created = UserNutritionGoal.objects.get_or_create(
        user=user,
        date=target_date,
        defaults={
            "daily_calories": cal_val,
            "daily_protein": prot_val,
            "daily_carbs": carb_val,
            "daily_fat": fat_val,
            "daily_water": water_val,
            "base_goal": base_val,
        },
    )

    # If an existing row was previously created with all 0s (uninitialized), populate it with defaults
    if not created and (
        (goal.daily_calories or 0) == 0
        and (goal.daily_protein or 0) == 0
        and (goal.daily_carbs or 0) == 0
        and (goal.daily_fat or 0) == 0
    ):
        goal.daily_calories = cal_val
        goal.daily_protein = prot_val
        goal.daily_carbs = carb_val
        goal.daily_fat = fat_val
        goal.daily_water = water_val
        goal.base_goal = base_val
        goal.save(update_fields=[
            "daily_calories",
            "daily_protein",
            "daily_carbs",
            "daily_fat",
            "daily_water",
            "base_goal",
        ])
    elif not created and update_if_default:
        for preset in DEFAULT_NUTRITION_GOALS.values():
            if (
                abs((goal.daily_calories or 0) - preset["calories_goal_kcal"]) < 0.01
                and abs((goal.daily_protein or 0) - preset["protein_goal_g"]) < 0.01
                and abs((goal.daily_carbs or 0) - preset["carbs_goal_g"]) < 0.01
                and abs((goal.daily_fat or 0) - preset["fat_goal_g"]) < 0.01
            ):
                goal.daily_calories = cal_val
                goal.daily_protein = prot_val
                goal.daily_carbs = carb_val
                goal.daily_fat = fat_val
                goal.daily_water = water_val
                goal.base_goal = base_val
                goal.save(update_fields=[
                    "daily_calories",
                    "daily_protein",
                    "daily_carbs",
                    "daily_fat",
                    "daily_water",
                    "base_goal",
                ])
                break

    return goal


def ensure_default_nutrition_goals_for_user(user, target_date=None, update_if_default: bool = False):
    """
    Seeds/ensures both `NutritionGoal` (nutritionX) and `UserNutritionGoal` (food_logger)
    for a user based on their gender ('male' or 'female').
    """
    nx_goal = ensure_default_nutritionx_goal(user, update_if_default=update_if_default)
    fl_goal = ensure_default_food_logger_goal(user, target_date=target_date, update_if_default=update_if_default)
    return nx_goal, fl_goal
