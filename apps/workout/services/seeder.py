import json
import logging
from pathlib import Path
from django.conf import settings
from django.db import transaction
from apps.core.tenants.context import bypass_tenant_isolation
from apps.workout.models import Exercise, Equipment, WorkoutTag, EQUIPMENT, MOVEMENT_PATTERNS

logger = logging.getLogger(__name__)

_CACHED_EXERCISE_DATA = None


def get_default_exercise_library_path() -> Path:
    return Path(settings.BASE_DIR) / 'apps' / 'workout' / 'exerciseLibrary.json'


def load_exercise_library_data(json_path=None):
    global _CACHED_EXERCISE_DATA
    if json_path is None and _CACHED_EXERCISE_DATA is not None:
        return _CACHED_EXERCISE_DATA

    target_path = Path(json_path) if json_path else get_default_exercise_library_path()
    if not target_path.exists():
        logger.warning(f"Exercise library JSON not found at: {target_path}")
        return []

    with open(target_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if isinstance(data, dict):
        items = list(data.values())
    elif isinstance(data, list):
        items = data
    else:
        items = []

    if json_path is None:
        _CACHED_EXERCISE_DATA = items

    return items


def seed_workout_library_for_tenant(tenant, stdout=None, json_path=None):
    """
    Seeds the complete Movement & Exercise library for a specific tenant,
    including equipment, movement patterns, exercises, and their relationships.
    Safe and idempotent: skips exercises that already exist for this tenant.
    """
    if stdout:
        stdout.write(f"Seeding Workout Movement Library for tenant: {tenant.name} ({tenant.subdomain})...")
    else:
        logger.info(f"Seeding Workout Movement Library for tenant: {tenant.name} ({tenant.subdomain})")

    items = load_exercise_library_data(json_path=json_path)
    if not items:
        msg = "No exercise data available to seed."
        if stdout:
            stdout.write(msg)
        else:
            logger.warning(msg)
        return {"created": 0, "skipped": 0, "equipment": 0, "tags": 0}

    with bypass_tenant_isolation():
        with transaction.atomic():
            # 1. Seed & cache Equipment
            existing_eq = {eq.name: eq for eq in Equipment.objects.filter(tenant=tenant)}
            new_eq = [Equipment(name=name, tenant=tenant) for name in EQUIPMENT.values() if name not in existing_eq]
            if new_eq:
                Equipment.objects.bulk_create(new_eq, ignore_conflicts=True)
                existing_eq = {eq.name: eq for eq in Equipment.objects.filter(tenant=tenant)}

            # 2. Seed & cache Movement Patterns (WorkoutTag)
            existing_tags = {tag.name: tag for tag in WorkoutTag.objects.filter(tenant=tenant)}
            new_tags = [WorkoutTag(name=name, tenant=tenant) for name in MOVEMENT_PATTERNS.values() if name not in existing_tags]

            # Also collect any general tags from items (e.g. Tag 1, Tag 2, etc.)
            general_tag_names = set()
            for item in items:
                for tag_id in item.get('tags', []):
                    # If tag_id not in movement patterns, label as Tag {tag_id}
                    if tag_id not in MOVEMENT_PATTERNS:
                        general_tag_names.add(f"Tag {tag_id}")

            for tag_name in general_tag_names:
                if tag_name not in existing_tags:
                    new_tags.append(WorkoutTag(name=tag_name, tenant=tenant))

            if new_tags:
                WorkoutTag.objects.bulk_create(new_tags, ignore_conflicts=True)
                existing_tags = {tag.name: tag for tag in WorkoutTag.objects.filter(tenant=tenant)}

            # 3. Create Exercises
            existing_ex_names = set(Exercise.objects.filter(tenant=tenant).values_list('name', flat=True))
            exercises_to_create = []
            seen_in_batch = set()

            for doc_idx, item in enumerate(items):
                raw_name = item.get("title") or item.get("titleAllCaps") or f"Exercise {doc_idx}"
                name = raw_name.strip()
                if not name or name in existing_ex_names or name in seen_in_batch:
                    continue

                seen_in_batch.add(name)
                desc = (item.get("benefits") or "").strip()
                cues = (item.get("trainerCues") or "").strip()
                media = item.get("media", [])
                video_url = media[0].strip() if media and isinstance(media, list) and media[0] else None

                exercises_to_create.append(Exercise(
                    tenant=tenant,
                    name=name,
                    description=desc,
                    coaching_cues=cues,
                    video_url=video_url
                ))

            created_count = len(exercises_to_create)
            skipped_count = len(items) - created_count

            if exercises_to_create:
                Exercise.objects.bulk_create(exercises_to_create, ignore_conflicts=True)

            # 4. Map relationships (equipment & tags) for all exercises
            all_tenant_exercises = {e.name: e.id for e in Exercise.objects.filter(tenant=tenant).only('id', 'name')}
            eq_through_objects = []
            tag_through_objects = []

            for item in items:
                raw_name = item.get("title") or item.get("titleAllCaps") or ""
                name = raw_name.strip()
                ex_id = all_tenant_exercises.get(name)
                if not ex_id:
                    continue

                # Equipment links
                for eq_id in item.get("equipment", []):
                    eq_name = EQUIPMENT.get(eq_id)
                    if eq_name and eq_name in existing_eq:
                        eq_through_objects.append(
                            Exercise.equipment.through(exercise_id=ex_id, equipment_id=existing_eq[eq_name].id)
                        )

                # Movement pattern links
                for mp_id in item.get("movementPatterns", []):
                    mp_name = MOVEMENT_PATTERNS.get(mp_id)
                    if mp_name and mp_name in existing_tags:
                        tag_through_objects.append(
                            Exercise.tags.through(exercise_id=ex_id, workouttag_id=existing_tags[mp_name].id)
                        )

                # General tag links
                for tag_id in item.get("tags", []):
                    if tag_id not in MOVEMENT_PATTERNS:
                        tag_name = f"Tag {tag_id}"
                        if tag_name in existing_tags:
                            tag_through_objects.append(
                                Exercise.tags.through(exercise_id=ex_id, workouttag_id=existing_tags[tag_name].id)
                            )

            if eq_through_objects:
                Exercise.equipment.through.objects.bulk_create(eq_through_objects, ignore_conflicts=True)

            if tag_through_objects:
                Exercise.tags.through.objects.bulk_create(tag_through_objects, ignore_conflicts=True)

    summary_msg = (
        f"Seeded workout library for '{tenant.name}': "
        f"{created_count} exercises created, {skipped_count} skipped/existing, "
        f"{len(existing_eq)} equipment, {len(existing_tags)} tags."
    )
    if stdout:
        stdout.write(summary_msg)
    else:
        logger.info(summary_msg)

    return {
        "created": created_count,
        "skipped": skipped_count,
        "equipment": len(existing_eq),
        "tags": len(existing_tags)
    }
