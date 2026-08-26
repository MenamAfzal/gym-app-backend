from django.core.management.base import BaseCommand
import stripe
from django.conf import settings
from apps.payments.models import BillingFeature, TenantBillingSubscription
from apps.core.tenants.context import bypass_tenant_isolation

stripe.api_key = settings.STRIPE_SECRET_KEY

KNOWN_FEATURES = [
    {
        "id": "3bb4f13a-1f0f-4ca0-9413-2925fa01c98f",
        "name": "Food Logger",
        "code": "food_logger",
        "description": "Full food diary with calorie tracking, macros, and meal history. Powered by the NutritionX database.",
        "price": "20.00",
        "billing_cycle": "monthly",
        "stripe_product_id": "prod_V7DEZTysBHY1pV",
        "stripe_price_id": "price_1U6yna46lb8eBWKKQGPgxp57",
        "is_active": True
    },
    {
        "id": "2dbe30c6-dd59-479f-951f-9047f2e66c1b",
        "name": "Workout Logger",
        "code": "workout_logger",
        "description": "You can access Workout feature",
        "price": "20.00",
        "billing_cycle": "monthly",
        "stripe_product_id": "prod_V75HwbVv4CA66F",
        "stripe_price_id": "price_1U6r6e46lb8eBWKKehwDCT79",
        "is_active": True
    },
    {
        "id": "3c5bd876-f4d4-4077-a877-933563247d58",
        "name": "Medication",
        "code": "medication",
        "description": "You can access Medication feature",
        "price": "20.00",
        "billing_cycle": "monthly",
        "stripe_product_id": "prod_V758fbg95Cmb3o",
        "stripe_price_id": "price_1U6qxx46lb8eBWKK8VVOkZbT",
        "is_active": True
    },
    {
        "id": "a13b847a-8792-4828-af64-079b54bd8517",
        "name": "Scheduling",
        "code": "scheduling",
        "description": "Smart class and trainer scheduling with booking management. Real-time slot management and automated reminders.",
        "price": "20.00",
        "billing_cycle": "monthly",
        "stripe_product_id": "prod_V7GEjX4iNM52ZS",
        "stripe_price_id": "price_1U71i346lb8eBWKK18YxLGxt",
        "is_active": True
    },
    {
        "id": "a1e47804-7ba8-4117-b5aa-3b2f97e42c41",
        "name": "Rooms",
        "code": "rooms",
        "description": "You can access Rooms feature",
        "price": "20.00",
        "billing_cycle": "monthly",
        "stripe_product_id": "prod_V755fzTuquT9ew",
        "stripe_price_id": "price_1U6qv146lb8eBWKKHaEFrFCk",
        "is_active": True
    },
    {
        "id": "42ca1d68-c41c-4f6c-a474-b4f94498873e",
        "name": "Multi-Location",
        "code": "multi_location",
        "description": "Exclusive access",
        "price": "20.00",
        "billing_cycle": "monthly",
        "stripe_product_id": "prod_V74XPOotWshWkc",
        "stripe_price_id": "price_1U71iL46lb8eBWKKEkSkSQ6v",
        "is_active": True
    },
    {
        "id": "b3ccae85-c594-4c64-8940-7a9b5029d503",
        "name": "Reflection Tracker",
        "code": "reflection_tracker",
        "description": "Personal wellness journal and progress reflection logging. Members can log moods, goals, and session notes.",
        "price": "20.00",
        "billing_cycle": "monthly",
        "stripe_product_id": "prod_V7GE144xDUCNQ9",
        "stripe_price_id": "price_1U71hs46lb8eBWKKyVRHTuvt",
        "is_active": True
    },
    {
        "id": "81f67870-4dee-49f9-80b6-0ec4cca7f8a7",
        "name": "News Feed",
        "code": "news_feed",
        "description": "Social gym news feed with posts, announcements, and community updates. Keep members engaged between sessions.",
        "price": "20.00",
        "billing_cycle": "monthly",
        "stripe_product_id": "prod_V7GEDbFh9vixFE",
        "stripe_price_id": "price_1U71hc46lb8eBWKK06e5qpKw",
        "is_active": True
    },
    {
        "id": "04483140-4e47-44e9-971e-7d69f7418c55",
        "name": "Notifications",
        "code": "notifications",
        "description": "You can access Notifications feature",
        "price": "20.00",
        "billing_cycle": "monthly",
        "stripe_product_id": "prod_V755oMKTHpop85",
        "stripe_price_id": "price_1U6qvl46lb8eBWKKouWTech7",
        "is_active": True
    },
    {
        "id": "da27f910-c2ff-4d0f-be9d-0ebcd81a942b",
        "name": "Water Logger",
        "code": "water_logger",
        "description": "Daily hydration tracking and water intake goal management. Help members stay hydrated and hit their wellness targets.",
        "price": "20.00",
        "billing_cycle": "monthly",
        "stripe_product_id": "prod_V1AmuUHTyPKqlr",
        "stripe_price_id": "price_1U18RD46lb8eBWKKDdCG81UQ",
        "is_active": True
    }
]

class Command(BaseCommand):
    help = "Restores deleted billing features with original UUIDs and heals relationships from Stripe subscriptions"

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("--- Starting Billing Features Restoration ---"))
        
        # 1. Restore/Recreate predefined known features
        for f in KNOWN_FEATURES:
            feat, created = BillingFeature.objects.get_or_create(
                id=f["id"],
                defaults={
                    "name": f["name"],
                    "code": f["code"],
                    "description": f["description"],
                    "price": f["price"],
                    "billing_cycle": f["billing_cycle"],
                    "stripe_product_id": f["stripe_product_id"],
                    "stripe_price_id": f["stripe_price_id"],
                    "is_active": f["is_active"]
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"✅ Recreated BillingFeature: {feat.name} ({feat.id})"))
            else:
                feat.name = f["name"]
                feat.code = f["code"]
                feat.description = f["description"]
                feat.price = f["price"]
                feat.billing_cycle = f["billing_cycle"]
                feat.stripe_product_id = f["stripe_product_id"]
                feat.stripe_price_id = f["stripe_price_id"]
                feat.is_active = f["is_active"]
                feat.save()
                self.stdout.write(f"ℹ️ Verified existing BillingFeature: {feat.name} ({feat.id})")

        # 2. Query all TenantBillingSubscription records and sync their active features from Stripe
        self.stdout.write(self.style.WARNING("\n--- Repairing Tenant Billing Subscriptions active features ---"))
        with bypass_tenant_isolation():
            subs = TenantBillingSubscription.objects.all()
            for sub in subs:
                if not sub.stripe_subscription_id:
                    continue
                
                self.stdout.write(f"\nProcessing subscription {sub.id} ({sub.stripe_subscription_id})")
                try:
                    stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)
                    stripe_sub_dict = stripe_sub.to_dict()
                    active_feature_ids = []
                    
                    for item in stripe_sub_dict.get("items", {}).get("data", []):
                        price_id = item.get("price", {}).get("id")
                        if not price_id:
                            continue
                        
                        feature = BillingFeature.objects.filter(stripe_price_id=price_id).first()
                        if not feature:
                            self.stdout.write(self.style.WARNING(f"⚠️ Found unknown Stripe Price ID {price_id} in subscription. Fetching from Stripe..."))
                            stripe_price = stripe.Price.retrieve(price_id)
                            stripe_product = stripe.Product.retrieve(stripe_price.product)
                            
                            price_amount = (stripe_price.unit_amount or 0) / 100.0
                            cycle = "monthly" if stripe_price.recurring and stripe_price.recurring.interval == "month" else "weekly"
                            
                            feature = BillingFeature.objects.create(
                                name=stripe_product.name,
                                code=stripe_product.name.lower().replace(" ", "_"),
                                description=getattr(stripe_product, "description", "") or "Dynamically recovered feature",
                                price=price_amount,
                                billing_cycle=cycle,
                                stripe_product_id=stripe_price.product,
                                stripe_price_id=price_id,
                                is_active=True
                            )
                            self.stdout.write(self.style.SUCCESS(f"🆕 Dynamically recovered and created BillingFeature: {feature.name} ({feature.id})"))
                        
                        active_feature_ids.append(feature.id)
                    
                    if active_feature_ids:
                        sub.active_features.set(active_feature_ids)
                        sub.save()
                        self.stdout.write(self.style.SUCCESS(f"✨ Successfully restored {len(active_feature_ids)} features for subscription {sub.stripe_subscription_id}!"))
                    else:
                        self.stdout.write(f"ℹ️ No active items found on Stripe for subscription {sub.stripe_subscription_id}.")
                        
                except stripe.error.StripeError as e:
                    self.stdout.write(self.style.ERROR(f"❌ Stripe error retrieving subscription {sub.stripe_subscription_id}: {str(e)}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"❌ Error recovering subscription {sub.id}: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("\n--- Restoration completed successfully! ---"))
