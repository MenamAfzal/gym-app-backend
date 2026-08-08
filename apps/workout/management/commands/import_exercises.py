import json
from django.core.management.base import BaseCommand, CommandError
from ...models import Exercise, Equipment, WorkoutTag
from ...models import MOVEMENT_PATTERNS, EQUIPMENT, Exercise
from apps.core.tenants.models import Tenant

class Command(BaseCommand):
    help = "Import exercises from Firestore JSON export (exerciseLibrary.json) into a specific Tenant context"

    def add_arguments(self, parser):
        parser.add_argument(
            "json_file",
            type=str,
            help="Path to Firestore exerciseLibrary.json file"
        )
        parser.add_argument(
            "--tenant",
            type=str,
            required=True,
            help="Subdomain or ID of the tenant to import exercises into"
        )

    def handle(self, *args, **options):
        json_file = options["json_file"]
        tenant_ident = options["tenant"]

        try:
            if "-" in tenant_ident:
                tenant = Tenant.objects.get(id=tenant_ident)
            else:
                tenant = Tenant.objects.get(subdomain=tenant_ident)
        except Tenant.DoesNotExist:
            raise CommandError(f"Tenant with subdomain/ID '{tenant_ident}' does not exist.")

        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise CommandError(f"File not found: {json_file}")
        except json.JSONDecodeError:
            raise CommandError(f"Invalid JSON format in: {json_file}")

        equipment_lookup = {}
        for _, name in EQUIPMENT.items():
            obj, _ = Equipment.objects.get_or_create(name=name, tenant=tenant)
            equipment_lookup[name] = obj

        movement_lookup = {}
        for _, name in MOVEMENT_PATTERNS.items():
            obj, _ = WorkoutTag.objects.get_or_create(name=name, tenant=tenant)
            movement_lookup[name] = obj

        if isinstance(data, dict):
            items = data.items()
        elif isinstance(data, list):
            items = [(str(i), doc) for i, doc in enumerate(data)]
        else:
            raise CommandError("Unsupported JSON format, must be dict or list")

        created, updated, skipped = 0, 0, 0
        skipped_details = []
        updated_details = []

        for doc_id, exercise_data in items:
            name = exercise_data.get("title") or exercise_data.get("titleAllCaps") or f"Exercise {doc_id}"

            description = exercise_data.get("benefits", "")
            video_url = None
            if exercise_data.get("media") and len(exercise_data["media"]) > 0:
                video_url = exercise_data["media"][0]

            coaching_cues = exercise_data.get("trainerCues", "")

            exercise, created_flag = Exercise.objects.get_or_create(
                name=name.strip(),
                tenant=tenant,
                defaults={
                    "description": description.strip() if description else "",
                    "video_url": video_url,
                    "coaching_cues": coaching_cues.strip() if coaching_cues else "",
                }
            )

            if not created_flag:
                needs_update = False
                if not exercise.description and description:
                    exercise.description = description.strip()
                    needs_update = True
                if not exercise.video_url and video_url:
                    exercise.video_url = video_url
                    needs_update = True
                if not exercise.coaching_cues and coaching_cues:
                    exercise.coaching_cues = coaching_cues.strip()
                    needs_update = True

                if needs_update:
                    exercise.save()
                    updated += 1
                    updated_details.append({
                        "doc_id": doc_id,
                        "name": name,
                        "reason": "Updated missing fields"
                    })
                else:
                    skipped += 1
                    skipped_details.append({
                        "doc_id": doc_id,
                        "name": name,
                        "reason": "Already exists with full data"
                    })
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

        self.stdout.write(self.style.SUCCESS("Import completed!"))
        self.stdout.write(self.style.SUCCESS(f"Exercises created: {created}"))
        self.stdout.write(self.style.SUCCESS(f"Exercises updated: {updated}"))
        self.stdout.write(self.style.SUCCESS(f"Exercises skipped: {skipped}"))
