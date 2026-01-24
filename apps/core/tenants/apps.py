from django.apps import AppConfig

class TenantsConfig(AppConfig):
    """Tenants app configuration."""
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.core.tenants'
    verbose_name = 'Tenants'

    def ready(self):
        """
        Import signals when app is ready.
        """
        import apps.core.tenants.signals
        