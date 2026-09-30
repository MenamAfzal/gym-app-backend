import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0016_appointment_credits_used_booking_credits_used_and_more'),
        ('workout', '0002_workout_assigned_user_workoutassignment'),
    ]

    operations = [
        migrations.AddField(
            model_name='workout',
            name='is_custom',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='workout',
            name='session',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='session_workouts', to='scheduling.classsession'),
        ),
    ]
