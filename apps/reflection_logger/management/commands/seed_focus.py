from django.utils.text import slugify
from django.core.management.base import BaseCommand
from ...models import FocusOption

class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        items = [
            ("Work", "work"),
            ("Strength Training", "strength"),
            ("Yoga", "yoga"),
            ("Relationships", "relationships"),
            ("Relaxation", "relaxation"),
            ("Active Rest", "active_rest"),
            ("Cleaning", "cleaning"),
            ("Cooking", "cooking"),
            ("Learning", "learning"),
            ("Reading", "reading"),
            ("Hydration", "hydration"),
            ("Power Walking", "power_walking"),
            ("Exploring", "exploring"),
            ("Family", "family"),
            ("Friends", "friends"),
            ("Fur Baby", "fur_baby"),
            ("Partner", "partner"),
            ("Solitude", "solitude"),
            ("Meditation", "meditation"),
            ("Organizing", "organizing"),
            ("Custom", "custom"),
        ]

        for name, icon in items:
            FocusOption.objects.get_or_create(
                name=name,
                defaults={
                    "icon": icon,
                    "slug": slugify(name)
                }
            )

        self.stdout.write(self.style.SUCCESS("Focus options seeded successfully."))
