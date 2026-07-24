from django.core.management.base import BaseCommand
from apps.reflection_logger.models import SymptomCategory, SymptomTag

class Command(BaseCommand):
    help = 'Seeds initial symptom categories and tags based on the provided UI designs'

    def handle(self, *args, **options):
        self.stdout.write("Seeding Symptom Data...")

        # 1. Define the Data Structure
        # Format: "Category Name": (Order, [List of Tags])
        data = {
            # Based on "Sex and sex drive" image
            "Sex and sex drive": (1, [
                "Didn't have sex",
                "Protected sex",
                "Unprotected sex",
                "Oral sex",
                "Anal sex",
                "Masturbation",
                "Sensual touch",
                "Sex toys",
                "Orgasm",
                "High sex drive",
                "Neutral sex drive",
                "Low sex drive"
            ]),
            # Based on "Mood" image
            "Mood": (2, [
                "Calm",
                "Happy",
                "Energetic",
                "Frisky",
                "Mood swings",
                "Irritated",
                "Sad",
                "Anxious",
                "Depressed",
                "Feeling guilty",
                "Obsessive thoughts",
                "Low energy",
                "Apathetic",
                "Confused",
                "Very self-critical"
            ]),
            # Based on "Symptoms" image
            "Symptoms": (3, [
                "Everything is fine",
                "Cramps",
                "Tender breasts",
                "Headache",
                "Acne",
                "Backache",
                "Fatigue",
                "Cravings",
                "Insomnia",
                "Abdominal pain",
                "Vaginal itching",
                "Vaginal dryness"
            ]),
            # Based on "Vaginal discharge" image
            "Vaginal discharge": (4, [
                "No discharge",
                "Creamy",
                "Watery",
                "Sticky",
                "Egg white",
                "Spotting",
                "Unusual",
                "Clumpy white",
                "Gray"
            ]),
            # Based on "Digestion and stool" image
            "Digestion and stool": (5, [
                "Nausea",
                "Bloating",
                "Constipation",
                "Diarrhea"
            ]),
            # Based on "Pregnancy test" image
            "Pregnancy test": (6, [
                "Didn't take tests",
                "Positive",
                "Negative",
                "Faint line"
            ]),
            # Based on "Other" image
            "Other": (7, [
                "Travel",
                "Stress",
                "Meditation",
                "Journaling",
                "Kegel exercises",
                "Breathing exercises",
                "Disease or injury",
                "Alcohol"
            ])
        }

        # 2. Iterate and Create
        for cat_name, (order, tags) in data.items():
            # Create Category
            category, created = SymptomCategory.objects.get_or_create(
                name=cat_name,
                defaults={'order': order, 'is_active': True}
            )
            
            if created:
                self.stdout.write(self.style.SUCCESS(f"Created Category: {cat_name}"))
            else:
                self.stdout.write(f"Category already exists: {cat_name}")

            # Create Tags for this Category
            for tag_name in tags:
                # We use user=None to indicate these are SYSTEM (Global) tags
                tag, tag_created = SymptomTag.objects.get_or_create(
                    category=category,
                    name=tag_name,
                    user=None,  # Explicitly set to None for global tags
                    defaults={'is_active': True}
                )

                if tag_created:
                    self.stdout.write(self.style.SUCCESS(f"  - Added Tag: {tag_name}"))

        self.stdout.write(self.style.SUCCESS("-----------------------------------"))
        self.stdout.write(self.style.SUCCESS("Seeding Completed Successfully!"))
        