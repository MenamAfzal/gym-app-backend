from django.apps import AppConfig


class WorkoutConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.workout'
    verbose_name = "Workout Module"

    def ready(self):
        try:
            import apps.workout.signals
        except ImportError:
            pass
