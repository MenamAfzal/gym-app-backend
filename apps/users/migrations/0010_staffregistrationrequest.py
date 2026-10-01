import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0006_tenant_logo'),
        ('users', '0009_managerpermissionpolicy'),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffRegistrationRequest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('email', models.EmailField(db_index=True, max_length=254)),
                ('password_hash', models.CharField(max_length=128)),
                ('role', models.CharField(choices=[('trainer', 'Trainer'), ('gym_manager', 'Gym Manager'), ('front_desk', 'Front Desk')], default='trainer', max_length=20)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], db_index=True, default='pending', max_length=20)),
                ('first_name', models.CharField(blank=True, max_length=100)),
                ('last_name', models.CharField(blank=True, max_length=100)),
                ('nickname', models.CharField(blank=True, max_length=50)),
                ('bio', models.TextField(blank=True, max_length=500)),
                ('profile_image', models.ImageField(blank=True, null=True, upload_to='staff_requests/%Y/%m/')),
                ('phone_number', models.CharField(blank=True, max_length=20)),
                ('date_of_birth', models.DateField(blank=True, null=True)),
                ('gender', models.CharField(blank=True, choices=[('male', 'Male'), ('female', 'Female'), ('other', 'Other'), ('prefer_not_to_say', 'Prefer not to say')], max_length=20)),
                ('height', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ('weight', models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ('address', models.CharField(blank=True, max_length=255)),
                ('city', models.CharField(blank=True, max_length=100)),
                ('country', models.CharField(blank=True, max_length=100)),
                ('postal_code', models.CharField(blank=True, max_length=20)),
                ('emergency_contact_name', models.CharField(blank=True, max_length=100)),
                ('emergency_contact_phone', models.CharField(blank=True, max_length=20)),
                ('specialization', models.CharField(blank=True, max_length=255)),
                ('experience_years', models.PositiveIntegerField(blank=True, null=True)),
                ('certifications', models.TextField(blank=True)),
                ('notes', models.TextField(blank=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('rejection_reason', models.TextField(blank=True, default='')),
                ('created_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='staff_registration_request', to=settings.AUTH_USER_MODEL)),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='reviewed_staff_requests', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(help_text='The tenant this record belongs to', on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)ss', to='tenants.tenant')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['tenant', 'status'], name='users_staff_tenant__ae2aa6_idx'), models.Index(fields=['email', 'tenant'], name='users_staff_email_a5971f_idx')],
            },
        ),
    ]
