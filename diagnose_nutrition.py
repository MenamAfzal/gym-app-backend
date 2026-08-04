import os
import django
import traceback

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.prod')
django.setup()

from apps.users.models import User
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant
from apps.nutritionX.serializers import BulkNutritionGoalSerializer

print("=== RUNNING NUTRITION DIAGNOSTIC DIRECTLY IN DB ===")

try:
    # 1. Get users
    staff = User.objects.get(email="teststaff@staff.com")
    client = User.objects.get(email="abdullahafzal2122@yopmail.com")
    tenant = Tenant.objects.get(id="5771abc2-61a1-404a-8d29-d6ee4331cb6e")
    
    # Set tenant context
    set_current_tenant(tenant)
    print(f"Loaded users. Tenant set to: {tenant.name}")

    # 2. Prepare payload
    data = {
        "goals": [
            {
                "user": str(client.id),
                "calories_goal_kcal": 2800.0,
                "protein_goal_g": 160.0,
                "carbs_goal_g": 220.0,
                "fat_goal_g": 80.0,
                "water_intake_goal_ml": "2500",
                "is_active": True
            }
        ]
    }

    # 3. Instantiate and run serializer
    serializer = BulkNutritionGoalSerializer(data=data)
    if serializer.is_valid():
        print("Serializer is valid. Saving...")
        created = serializer.save()
        print("Success! Created goals:", created)
    else:
        print("Serializer Validation Errors:", serializer.errors)

except Exception as e:
    print("\n!!! ERROR CAPTURED !!!")
    traceback.print_exc()

print("==================================================")
