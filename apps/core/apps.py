from django.apps import AppConfig


class CoreConfig(AppConfig):
    """
    Core app configuration.
    Houses multi-tenant infrastructure, middleware, and shared services.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core'
    verbose_name = 'Core'
