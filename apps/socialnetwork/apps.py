from django.apps import AppConfig


class SocialnetworkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.socialnetwork"
    verbose_name = "Social Network"

    def ready(self):
        import apps.socialnetwork.views
    
  
