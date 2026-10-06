import logging
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F
from apps.scheduling.models import Booking, Package, PackageType
from apps.core.tenants.context import bypass_tenant_isolation

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill retention schema fields: Package.total_credits_allocated and Booking.cancelled_at"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting retention schema backfill..."))

        with bypass_tenant_isolation():
            # 1. Backfill Package total_credits_allocated
            with transaction.atomic():
                pkg_count = Package.all_objects.filter(total_credits_allocated__isnull=True).count()
                updated_pkg = 0
                for pkg_type in PackageType.all_objects.all():
                    updated = Package.all_objects.filter(
                        package_type=pkg_type,
                        total_credits_allocated__isnull=True
                    ).update(total_credits_allocated=pkg_type.credit_count)
                    updated_pkg += updated

                # Fallback to credits_remaining if package_type is missing or credit_count was 0/null
                fallback_pkg = Package.all_objects.filter(
                    total_credits_allocated__isnull=True
                ).update(
                    total_credits_allocated=F('credits_remaining')
                )

                self.stdout.write(self.style.SUCCESS(
                    f"Successfully backfilled total_credits_allocated for {updated_pkg + fallback_pkg} packages (found {pkg_count})."
                ))

            # 2. Backfill Booking cancelled_at
            with transaction.atomic():
                cancelled_bookings = Booking.all_objects.filter(
                    status='cancelled',
                    cancelled_at__isnull=True
                )
                bk_count = cancelled_bookings.count()

                # Use updated_at as default cancelled_at
                updated_bk = cancelled_bookings.update(
                    cancelled_at=F('updated_at')
                )

                self.stdout.write(self.style.SUCCESS(
                    f"Successfully backfilled cancelled_at for {updated_bk} cancelled bookings (found {bk_count})."
                ))

        self.stdout.write(self.style.SUCCESS("Retention schema backfill completed successfully."))
