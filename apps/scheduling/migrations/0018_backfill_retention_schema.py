from django.db import migrations
from django.db.models import F


def backfill_data(apps, schema_editor):
    Package = apps.get_model('scheduling', 'Package')
    PackageType = apps.get_model('scheduling', 'PackageType')
    Booking = apps.get_model('scheduling', 'Booking')

    # 1. Backfill Package total_credits_allocated by package_type
    for pkg_type in PackageType.objects.all():
        Package.objects.filter(
            package_type=pkg_type,
            total_credits_allocated__isnull=True
        ).update(total_credits_allocated=pkg_type.credit_count)

    # Fallback to credits_remaining for any remaining packages
    Package.objects.filter(
        total_credits_allocated__isnull=True
    ).update(total_credits_allocated=F('credits_remaining'))

    # 2. Backfill Booking cancelled_at
    Booking.objects.filter(
        status='cancelled',
        cancelled_at__isnull=True
    ).update(cancelled_at=F('updated_at'))


def reverse_backfill(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0017_booking_cancellation_reason_booking_cancelled_at_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_data, reverse_backfill),
    ]
