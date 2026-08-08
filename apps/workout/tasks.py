import os
from celery import shared_task
from apps.workout.models import Exercise
from apps.workout.local_storage import save_file_locally

@shared_task
def upload_video_to_s3_task(file_path, exercise_id, key=None):
    if not os.path.exists(file_path):
        Exercise.objects.filter(id=exercise_id).update(upload_status="failed")
        return f"File {file_path} not found"

    try:
        rel_path, file_url = save_file_locally(file_path, folder="videos")

        Exercise.objects.filter(id=exercise_id).update(
            video_url=file_url,
            video_file=rel_path,
            upload_status="uploaded"
        )
        return f"Video saved locally: {file_url}"

    except Exception as e:
        Exercise.objects.filter(id=exercise_id).update(upload_status="failed")
        return f"Local file save failed: {e}"
        
    finally:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
