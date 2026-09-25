import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('socialnetwork', '0002_alter_comment_tenant_alter_commentreaction_tenant_and_more'),
        ('tenants', '0006_tenant_logo'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SocialPost',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('caption', models.CharField(blank=True, max_length=2400, null=True)),
                ('likes_count', models.IntegerField(blank=True, default=0, null=True)),
                ('comments_count', models.IntegerField(blank=True, default=0, null=True)),
                ('external_link', models.URLField(blank=True, null=True)),
                ('internal_deep_link', models.CharField(blank=True, max_length=255, null=True)),
                ('visible_to_staff', models.BooleanField(blank=True, default=True, null=True)),
                ('visible_to_clients', models.BooleanField(blank=True, default=True, null=True)),
                ('location', models.CharField(blank=True, max_length=255, null=True)),
                ('comments_enabled', models.BooleanField(blank=True, default=True, null=True)),
                ('tenant', models.ForeignKey(help_text='The tenant this record belongs to', on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)ss', to='tenants.tenant')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(class)s_posts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PostMedia',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file', models.FileField(upload_to='posts/')),
                ('media_type', models.CharField(choices=[('image', 'Image'), ('video', 'Video')], max_length=10)),
                ('order', models.PositiveIntegerField(default=0)),
                ('duration', models.DurationField(blank=True, null=True)),
                ('tenant', models.ForeignKey(help_text='The tenant this record belongs to', on_delete=django.db.models.deletion.CASCADE, related_name='%(app_label)s_%(class)ss', to='tenants.tenant')),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='media_items', to='socialnetwork.socialpost')),
            ],
            options={
                'ordering': ['order', 'created_at'],
            },
        ),
    ]
