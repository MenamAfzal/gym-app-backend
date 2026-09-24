import django.core.validators
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0015_alter_booking_options_event_eventsession_and_more'),
        ('tenants', '0006_tenant_logo'),
    ]

    operations = [
        migrations.AddField(
            model_name='appointment',
            name='credits_used',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='booking',
            name='credits_used',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.CreateModel(
            name='TenantBookingSettings',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('session_late_cancellation_hours', models.PositiveIntegerField(default=12, validators=[django.core.validators.MinValueValidator(0)])),
                ('session_booking_credits', models.PositiveIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)])),
                ('appointment_late_cancellation_hours', models.PositiveIntegerField(default=12, validators=[django.core.validators.MinValueValidator(0)])),
                ('appointment_booking_credits', models.PositiveIntegerField(default=1, validators=[django.core.validators.MinValueValidator(1)])),
                ('tenant', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='booking_settings', to='tenants.tenant')),
            ],
            options={
                'verbose_name': 'Tenant Booking Settings',
                'verbose_name_plural': 'Tenant Booking Settings',
            },
        ),
    ]
