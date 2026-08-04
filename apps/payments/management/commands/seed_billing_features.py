"""
Management Command: seed_billing_features
=========================================

Seeds the 5 premium BillingFeature rows for the Gym Management Platform.
Each feature maps 1-to-1 with a Stripe Product/Price.

Usage:
    python manage.py seed_billing_features           # insert or update
    python manage.py seed_billing_features --dry-run # preview only
"""

from django.core.management.base import BaseCommand
from django.db import transaction


# ---------------------------------------------------------------------------
# Feature definitions — update stripe_price_id here if prices ever change
# ---------------------------------------------------------------------------
BILLING_FEATURES = [
    {
        "name": "News Feed",
        "code": "news_feed",
        "description": (
            "Social gym news feed with posts, announcements, and community updates. "
            "Keep members engaged between sessions."
        ),
        "stripe_price_id": "price_1U0oMV46lb8eBWKKiZhIXyMD",
        "is_active": True,
    },
    {
        "name": "Water Logger",
        "code": "water_logger",
        "description": (
            "Daily hydration tracking and water intake goal management. "
            "Help members stay hydrated and hit their wellness targets."
        ),
        "stripe_price_id": "price_1U0oGv46lb8eBWKKJyAR0wA5",
        "is_active": True,
    },
    {
        "name": "Food Logger",
        "code": "food_logger",
        "description": (
            "Full food diary with calorie tracking, macros, and meal history. "
            "Powered by the NutritionX database."
        ),
        "stripe_price_id": "price_1U0oFZ46lb8eBWKKoaieRSbO",
        "is_active": True,
    },
    {
        "name": "Reflection Tracker",
        "code": "reflection_tracker",
        "description": (
            "Personal wellness journal and progress reflection logging. "
            "Members can log moods, goals, and session notes."
        ),
        "stripe_price_id": "price_1U0oEE46lb8eBWKKjlXzWXNP",
        "is_active": True,
    },
    {
        "name": "Scheduling",
        "code": "scheduling",
        "description": (
            "Smart class and trainer scheduling with booking management. "
            "Real-time slot management and automated reminders."
        ),
        "stripe_price_id": "price_1U0mZJ46lb8eBWKKoVoJXcXO",
        "is_active": True,
    },
]


class Command(BaseCommand):
    help = (
        "Seeds the 5 premium BillingFeature rows with their Stripe Price IDs. "
        "Uses get_or_create so it is safe to run multiple times."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview what would be created/updated without touching the DB.",
        )

    def handle(self, *args, **options):
        from apps.payments.models import BillingFeature

        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no DB changes will be made.\n"))

        created_count = 0
        updated_count = 0
        skipped_count = 0

        with transaction.atomic():
            for feature_data in BILLING_FEATURES:
                code = feature_data["code"]

                if dry_run:
                    existing = BillingFeature.objects.filter(code=code).first()
                    if existing:
                        if existing.stripe_price_id != feature_data["stripe_price_id"]:
                            self.stdout.write(
                                f"  WOULD UPDATE  [{code}]  "
                                f"price_id: {existing.stripe_price_id} → {feature_data['stripe_price_id']}"
                            )
                        else:
                            self.stdout.write(f"  SKIP (up to date)  [{code}] {feature_data['name']}")
                    else:
                        self.stdout.write(f"  WOULD CREATE  [{code}] {feature_data['name']}")
                    continue

                # Real insert / update
                feature, created = BillingFeature.objects.get_or_create(
                    code=code,
                    defaults={
                        "name": feature_data["name"],
                        "description": feature_data["description"],
                        "stripe_price_id": feature_data["stripe_price_id"],
                        "is_active": feature_data["is_active"],
                    },
                )

                if created:
                    created_count += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✅ CREATED  [{code}] {feature.name}  —  {feature.stripe_price_id}"
                        )
                    )
                else:
                    # Check if price_id needs updating
                    changed = False
                    if feature.stripe_price_id != feature_data["stripe_price_id"]:
                        feature.stripe_price_id = feature_data["stripe_price_id"]
                        changed = True
                    if feature.name != feature_data["name"]:
                        feature.name = feature_data["name"]
                        changed = True
                    if feature.description != feature_data["description"]:
                        feature.description = feature_data["description"]
                        changed = True
                    if feature.is_active != feature_data["is_active"]:
                        feature.is_active = feature_data["is_active"]
                        changed = True

                    if changed:
                        feature.save()
                        updated_count += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"  🔄 UPDATED  [{code}] {feature.name}  —  {feature.stripe_price_id}"
                            )
                        )
                    else:
                        skipped_count += 1
                        self.stdout.write(
                            f"  ─  SKIP (no change)  [{code}] {feature.name}"
                        )

        if not dry_run:
            self.stdout.write("")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Done: {created_count} created, {updated_count} updated, {skipped_count} skipped."
                )
            )

            # Final DB summary
            self.stdout.write("")
            self.stdout.write("Current BillingFeatures in DB:")
            self.stdout.write("─" * 70)
            for f in BillingFeature.objects.order_by("name"):
                status = "✅ active" if f.is_active else "⛔ inactive"
                self.stdout.write(
                    f"  {status}  [{f.code}]\n"
                    f"           Name:     {f.name}\n"
                    f"           Price ID: {f.stripe_price_id}\n"
                    f"           UUID:     {f.id}\n"
                )
