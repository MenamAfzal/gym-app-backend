from django.utils.text import slugify
from django.core.management.base import BaseCommand
from apps.core.tenants.models import Tenant
from ...models import FocusOption

class Command(BaseCommand):
    help = "Seeds initial focus options for all tenants"

    def handle(self, *args, **kwargs):
        tenants = Tenant.objects.all()
        if not tenants.exists():
            self.stdout.write(self.style.WARNING("No tenants found in the database. Please create a tenant first."))
            return

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

        for tenant in tenants:
            self.stdout.write(f"Seeding Focus options for tenant: {tenant.name} ({tenant.id})")
            for name, icon in items:
                # Use all_objects to bypass the TenantAwareManager context filters
                FocusOption.all_objects.get_or_create(
                    tenant=tenant,
                    name=name,
                    user=None,  # None indicates system-wide defaults
                    defaults={
                        "icon": icon,
                        "slug": slugify(name)
                    }
                )

        self.stdout.write(self.style.SUCCESS("Focus options seeded successfully."))
