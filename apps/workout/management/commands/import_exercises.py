import json
from django.core.management.base import BaseCommand, CommandError
from ...models import Exercise, Equipment, WorkoutTag
from ...models import MOVEMENT_PATTERNS, EQUIPMENT, Exercise
from apps.core.tenants.models import Tenant

class Command(BaseCommand):
    help = "Import exercises from Firestore JSON export (exerciseLibrary.json) into one or all Tenants"

    def add_arguments(self, parser):
        parser.add_argument(
            "json_file",
            type=str,
            help="Path to Firestore exerciseLibrary.json file"
        )
        parser.add_argument(
            "--tenant",
            type=str,
            required=False,
            help="Subdomain or ID of a specific tenant. If omitted, imports into ALL tenants."
        )

    def handle(self, *args, **options):
        from apps.core.tenants.context import _bypass_isolation
        _bypass_isolation.set(True)

        json_file = options["json_file"]
        tenant_ident = options.get("tenant")

        if tenant_ident:
            try:
                if "-" in tenant_ident:
                    tenants = [Tenant.objects.get(id=tenant_ident)]
                else:
                    tenants = [Tenant.objects.get(subdomain=tenant_ident)]
            except Tenant.DoesNotExist:
                raise CommandError(f"Tenant '{tenant_ident}' does not exist.")
        else:
            tenants = list(Tenant.objects.all())
            if not tenants:
                raise CommandError("No tenants found in the database. Create a tenant first.")

        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise CommandError(f"File not found: {json_file}")
        except json.JSONDecodeError:
            raise CommandError(f"Invalid JSON format in: {json_file}")

        if isinstance(data, dict):
            items = data.items()
        elif isinstance(data, list):
            items = [(str(i), doc) for i, doc in enumerate(data)]
        else:
            raise CommandError("Unsupported JSON format, must be dict or list")

        for tenant in tenants:
            self.stdout.write(self.style.NOTICE(f"Processing tenant: {tenant.subdomain} (ID: {tenant.id})"))
            
            equipment_lookup = {}
            for _, name in EQUIPMENT.items():
                obj, _ = Equipment.objects.get_or_create(name=name, tenant=tenant)
                equipment_lookup[name] = obj

            movement_lookup = {}
            for _, name in MOVEMENT_PATTERNS.items():
                obj, _ = WorkoutTag.objects.get_or_create(name=name, tenant=tenant)
                movement_lookup[name] = obj

            created, updated, skipped = 0, 0, 0

            for doc_id, exercise_data in items:
                name = exercise_data.get("title") or exercise_data.get("titleAllCaps") or f"Exercise {doc_id}"
                description = exercise_data.get("benefits", "")
                coaching_cues = exercise_data.get("trainerCues", "")

                exercise, created_flag = Exercise.objects.get_or_create(
                    name=name.strip(),
                    tenant=tenant,
                    defaults={
                        "description": description.strip() if description else "",
                        "video_url": None,
                        "coaching_cues": coaching_cues.strip() if coaching_cues else "",
                    }
                )

                if not created_flag:
                    needs_update = False
                    if not exercise.description and description:
                        exercise.description = description.strip()
                        needs_update = True
                    if not exercise.coaching_cues and coaching_cues:
                        exercise.coaching_cues = coaching_cues.strip()
                        needs_update = True

                    if needs_update:
                        exercise.save()
                        updated += 1
                    else:
                        skipped += 1
                    continue

                created += 1

                for eq_id in exercise_data.get("equipment", []):
                    eq_name = EQUIPMENT.get(eq_id)
                    if eq_name:
                        exercise.equipment.add(equipment_lookup[eq_name])

                for mp_id in exercise_data.get("movementPatterns", []):
                    mp_name = MOVEMENT_PATTERNS.get(mp_id)
                    if mp_name:
                        exercise.tags.add(movement_lookup[mp_name])

                for tag_id in exercise_data.get("tags", []):
                    tag_name = f"Tag {tag_id}"
                    tag_obj, _ = WorkoutTag.objects.get_or_create(name=tag_name, tenant=tenant)
                    exercise.tags.add(tag_obj)

            self.stdout.write(self.style.SUCCESS(f"Import completed for {tenant.subdomain}! Created: {created}, Updated: {updated}, Skipped: {skipped}"))
