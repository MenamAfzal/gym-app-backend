import uuid
from django.core.management.base import BaseCommand, CommandError
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import bypass_tenant_isolation
from apps.core.tenants.provisioner import TenantProvisioningService


class Command(BaseCommand):
    help = (
        "Synchronizes and seeds all default tenant modules and data (Reflection symptoms & tags, "
        "Reflection focus options, Workout movement library, Booking settings, Loyalty reward program, "
        "and Free billing plan) for one or all tenants."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            required=False,
            help="Subdomain or UUID of a specific tenant. If omitted, syncs ALL tenants in the database."
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

            self.stdout.write(self.style.NOTICE(f"=== Starting Tenant Provisioning for {len(tenants)} tenant(s) ==="))

            success_count = 0
            for idx, tenant in enumerate(tenants, 1):
                self.stdout.write(f"\n[{idx}/{len(tenants)}] Provisioning: {tenant.name} (Subdomain: {tenant.subdomain}, ID: {tenant.id})")
                try:
                    TenantProvisioningService.provision_new_tenant(tenant, stdout=self.stdout)
                    success_count += 1
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"Error provisioning tenant {tenant.subdomain}: {e}"))

            self.stdout.write(self.style.SUCCESS(f"\n=== Successfully provisioned {success_count}/{len(tenants)} tenant(s) ==="))
