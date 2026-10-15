import uuid
from django.core.management.base import BaseCommand, CommandError
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import bypass_tenant_isolation
from apps.reflection_logger.services.seeder import seed_focus_for_tenant


class Command(BaseCommand):
    help = "Seeds initial focus options for one or all tenants"

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            required=False,
            help="Subdomain or UUID of a specific tenant. If omitted, seeds for ALL tenants."
        )

    def handle(self, *args, **options):
        tenant_ident = options.get("tenant")

        with bypass_tenant_isolation():
            if tenant_ident:
                try:
                    try:
                        tenant_uuid = uuid.UUID(str(tenant_ident))
                        tenants = [Tenant.objects.get(id=tenant_uuid)]
                    except (ValueError, AttributeError):
                        tenants = [Tenant.objects.get(subdomain=tenant_ident)]
                except Tenant.DoesNotExist:
                    raise CommandError(f"Tenant '{tenant_ident}' does not exist.")
            else:
                tenants = list(Tenant.objects.all())
                if not tenants:
                    self.stdout.write(self.style.WARNING("No tenants found in the database. Please create a tenant first."))
                    return

            for tenant in tenants:
                seed_focus_for_tenant(tenant, stdout=self.stdout)

            self.stdout.write(self.style.SUCCESS(f"Focus options seeded successfully for {len(tenants)} tenant(s)."))
