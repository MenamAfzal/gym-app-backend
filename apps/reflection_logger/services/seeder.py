import logging
from django.utils.text import slugify
from apps.reflection_logger.models import SymptomCategory, SymptomTag, FocusOption

logger = logging.getLogger(__name__)

def seed_symptoms_for_tenant(tenant, stdout=None):
    """
    Seeds initial symptom categories and tags for a specific tenant.
    """
    data = {
        "Sex and sex drive": (1, [
            "Didn't have sex", "Protected sex", "Unprotected sex", "Oral sex",
            "Anal sex", "Masturbation", "Sensual touch", "Sex toys", "Orgasm",
            "High sex drive", "Neutral sex drive", "Low sex drive"
        ]),
        "Mood": (2, [
            "Calm", "Happy", "Energetic", "Frisky", "Mood swings", "Irritated",
            "Sad", "Anxious", "Depressed", "Feeling guilty", "Obsessive thoughts",
            "Low energy", "Apathetic", "Confused", "Very self-critical"
        ]),
        "Symptoms": (3, [
            "Everything is fine", "Cramps", "Tender breasts", "Headache", "Acne",
            "Backache", "Fatigue", "Cravings", "Insomnia", "Abdominal pain",
            "Vaginal itching", "Vaginal dryness"
        ]),
        "Vaginal discharge": (4, [
            "No discharge", "Creamy", "Watery", "Sticky", "Egg white",
            "Spotting", "Unusual", "Clumpy white", "Gray"
        ]),
        "Digestion and stool": (5, [
            "Nausea", "Bloating", "Constipation", "Diarrhea"
        ]),
        "Pregnancy test": (6, [
            "Didn't take tests", "Positive", "Negative", "Faint line"
        ]),
        "Other": (7, [
            "Travel", "Stress", "Meditation", "Journaling", "Kegel exercises",
            "Breathing exercises", "Disease or injury", "Alcohol"
        ])
    }

    if stdout:
        stdout.write(f"Seeding Symptom Data for tenant: {tenant.name} ({tenant.id})")
    else:
        logger.info(f"Seeding Symptom Data for tenant: {tenant.name} ({tenant.id})")

    for cat_name, (order, tags) in data.items():
        category, created = SymptomCategory.all_objects.get_or_create(
            tenant=tenant,
            name=cat_name,
            defaults={'order': order, 'is_active': True}
        )
        
        if created and stdout:
            stdout.write(f"  Created Category: {cat_name}")

        for tag_name in tags:
            tag, tag_created = SymptomTag.all_objects.get_or_create(
                tenant=tenant,
                category=category,
                name=tag_name,
                user=None,  # System tag
                defaults={'is_active': True}
            )
            if tag_created and stdout:
                stdout.write(f"    - Added Tag: {tag_name}")


def seed_focus_for_tenant(tenant, stdout=None):
    """
    Seeds initial focus options for a specific tenant.
    """
    items = [
        ("Work", "work"), ("Strength Training", "strength"), ("Yoga", "yoga"),
        ("Relationships", "relationships"), ("Relaxation", "relaxation"),
        ("Active Rest", "active_rest"), ("Cleaning", "cleaning"),
        ("Cooking", "cooking"), ("Learning", "learning"), ("Reading", "reading"),
        ("Hydration", "hydration"), ("Power Walking", "power_walking"),
        ("Exploring", "exploring"), ("Family", "family"), ("Friends", "friends"),
        ("Fur Baby", "fur_baby"), ("Partner", "partner"), ("Solitude", "solitude"),
        ("Meditation", "meditation"), ("Organizing", "organizing"), ("Custom", "custom"),
    ]

    if stdout:
        stdout.write(f"Seeding Focus options for tenant: {tenant.name} ({tenant.id})")
    else:
        logger.info(f"Seeding Focus options for tenant: {tenant.name} ({tenant.id})")

    for name, icon in items:
        FocusOption.all_objects.get_or_create(
            tenant=tenant,
            name=name,
            user=None,
            defaults={
                "icon": icon,
                "slug": slugify(name)
            }
        )
