"""
Celery Configuration

Configures Celery app for background task processing.
All tasks are tenant-aware and use Redis as broker/backend.
"""
import os
from celery import Celery

# Set default Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')

# Create Celery app
app = Celery('gym_app')

# Load config from Django settings with 'CELERY_' prefix
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from all installed apps
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    """Debug task to test Celery setup."""
    print(f'Request: {self.request!r}')


@app.task(bind=True)
def sample_tenant_task(self, tenant_id, **kwargs):
    """
    Sample tenant-aware task.
    
    Args:
        tenant_id: UUID of tenant
        **kwargs: Additional task arguments
        
    Example:
        from apps.core.tenants.models import Tenant
        tenant = Tenant.objects.get(id=tenant_id)
        # Do tenant-specific work here
    """
    from apps.core.tenants.models import Tenant
    
    tenant = Tenant.objects.get(id=tenant_id)
    print(f'Processing task for tenant: {tenant.name}')
    
    # Your tenant-aware logic here
    return f'Task completed for tenant {tenant.name}'
