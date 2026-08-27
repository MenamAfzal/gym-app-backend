from django.db import migrations

def backfill_package_prices(apps, schema_editor):
    Package = apps.get_model('scheduling', 'Package')
    for pkg in Package.objects.filter(price__isnull=True):
        if pkg.package_type:
            pkg.price = pkg.package_type.price
            pkg.save(update_fields=['price'])

class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0007_package_price'),
    ]

    operations = [
        migrations.RunPython(backfill_package_prices, reverse_code=migrations.RunPython.noop),
    ]
