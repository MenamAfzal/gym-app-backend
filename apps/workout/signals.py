from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from django.utils.timezone import localtime

from apps.notifications.utils import send_push_to_user
from apps.workout.local_storage import delete_local_file
from apps.workout.models import Exercise, WorkoutLog


@receiver(post_delete, sender=Exercise)
def delete_video_from_storage(sender, instance, **kwargs):
    if instance.video_url:
        delete_local_file(instance.video_url)
    if instance.video_file:
        delete_local_file(instance.video_file.name)

delete_video_from_s3 = delete_video_from_storage

@receiver(post_save, sender=WorkoutLog)
def notify_client_of_prescribed_workout(sender, instance, created, **kwargs):
    if created and instance.session:
        local_start_time = localtime(instance.session.start_time)
        day_of_the_week = local_start_time.strftime("%A")
        
        title = "For your eyes only"
        body = f"We've uploaded your prescribed workout for {day_of_the_week}. Take a look and we'll dial it in further during your session!"
        
        send_push_to_user(
            user=instance.user,
            title=title,
            body=body,
            screen="workout_log",
            object_id=instance.workout.id
        )