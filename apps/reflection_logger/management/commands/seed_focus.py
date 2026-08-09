from django.core.management.base import BaseCommand
from apps.core.tenants.models import Tenant
from apps.reflection_logger.services.seeder import seed_focus_for_tenant

class Command(BaseCommand):
    help = "Seeds initial focus options for all tenants"

    def handle(self, *args, **kwargs):
        tenants = Tenant.objects.all()
        if not tenants.exists():
            self.stdout.write(self.style.WARNING("No tenants found in the database. Please create a tenant first."))
            return

        for tenant in tenants:
            seed_focus_for_tenant(tenant, stdout=self.stdout)

        self.stdout.write(self.style.SUCCESS("Focus options seeded successfully."))
