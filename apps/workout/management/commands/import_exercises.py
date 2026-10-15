import uuid
from django.core.management.base import BaseCommand, CommandError
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import bypass_tenant_isolation
from apps.workout.services.seeder import seed_workout_library_for_tenant, get_default_exercise_library_path


class Command(BaseCommand):
    help = "Import workout movement library (exercises, equipment, movement patterns) into one or all Tenants"

    def add_arguments(self, parser):
        parser.add_argument(
            "json_file_pos",
            nargs="?",
            default=None,
            help="Optional positional path to exerciseLibrary.json file"
        )
        parser.add_argument(
            "--file",
            type=str,
            dest="json_file_flag",
            default=None,
            help="Path to exerciseLibrary.json file (defaults to apps/workout/exerciseLibrary.json)"
        )
        parser.add_argument(
            "--tenant",
            type=str,
            required=False,
            help="Subdomain or UUID of a specific tenant. If omitted, imports into ALL tenants."
        )

    def handle(self, *args, **options):
        json_file = options.get("json_file_flag") or options.get("json_file_pos")
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

            default_path = get_default_exercise_library_path()
            active_path = json_file or str(default_path)

            self.stdout.write(self.style.NOTICE(f"Importing exercise library from: {active_path}"))
            self.stdout.write(self.style.NOTICE(f"Targeting {len(tenants)} tenant(s)..."))

            total_created = 0
            for tenant in tenants:
                self.stdout.write(f"\nProcessing tenant: {tenant.name} ({tenant.subdomain})")
                try:
                    stats = seed_workout_library_for_tenant(
                        tenant=tenant,
                        stdout=self.stdout,
                        json_path=active_path
                    )
                    total_created += stats.get("created", 0)
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"Error seeding workout library for {tenant.subdomain}: {e}"))

            self.stdout.write(self.style.SUCCESS(f"\nSuccessfully finished! Total new exercises created: {total_created}"))
