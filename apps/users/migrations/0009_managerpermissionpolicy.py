import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0006_tenant_logo'),
        ('users', '0008_pendingregistration_referral_code_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ManagerPermissionPolicy',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('permissions', models.JSONField(default=dict)),
                ('has_full_access', models.BooleanField(default=False)),
                ('manager', models.OneToOneField(limit_choices_to={'role': 'gym_manager'}, on_delete=django.db.models.deletion.CASCADE, related_name='permission_policy', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(help_text='The tenant this record belongs to', on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)ss', to='tenants.tenant')),
            ],
            options={
                'unique_together': {('tenant', 'manager')},
            },
        ),
    ]
