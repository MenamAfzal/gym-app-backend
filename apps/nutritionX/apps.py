from django.apps import AppConfig


class NutritionxConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.nutritionX'

    def ready(self):
        import apps.nutritionX.signals
