from django.apps import AppConfig


class RewardsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.rewards'
    verbose_name = 'Rewards & Loyalty Engine'

    def ready(self):
        import apps.rewards.signals  # noqa
