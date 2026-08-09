from django.core.management.base import BaseCommand
from apps.core.tenants.models import Tenant
from apps.reflection_logger.services.seeder import seed_symptoms_for_tenant

class Command(BaseCommand):
    help = 'Seeds initial symptom categories and tags based on the provided UI designs for all tenants'

    def handle(self, *args, **options):
        self.stdout.write("Seeding Symptom Data...")

        tenants = Tenant.objects.all()
        if not tenants.exists():
            self.stdout.write(self.style.WARNING("No tenants found in the database. Please create a tenant first."))
            return

        for tenant in tenants:
            seed_symptoms_for_tenant(tenant, stdout=self.stdout)

        self.stdout.write(self.style.SUCCESS("-----------------------------------"))
        self.stdout.write(self.style.SUCCESS("Seeding Completed Successfully!"))
        