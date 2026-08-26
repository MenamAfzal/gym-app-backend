from django.db import migrations

def update_basic_plan(apps, schema_editor):
    BillingPlan = apps.get_model("payments", "BillingPlan")
    try:
        basic_plan = BillingPlan.objects.get(slug="basic")
        basic_plan.allowed_feature_count = 5
        basic_plan.save()
    except BillingPlan.DoesNotExist:
        pass

def reverse_basic_plan(apps, schema_editor):
    BillingPlan = apps.get_model("payments", "BillingPlan")
    try:
        basic_plan = BillingPlan.objects.get(slug="basic")
        basic_plan.allowed_feature_count = 3
        basic_plan.save()
    except BillingPlan.DoesNotExist:
        pass

class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0005_billingfeature_billing_cycle_billingfeature_price_and_more"),
    ]

    operations = [
        migrations.RunPython(update_basic_plan, reverse_code=reverse_basic_plan),
    ]
