import logging
import stripe
from django.conf import settings
from apps.scheduling.models import PackageType
from apps.core.tenants.context import bypass_tenant_isolation

logger = logging.getLogger(__name__)
stripe.api_key = settings.STRIPE_SECRET_KEY

class StripePackageService:
    @staticmethod
    def sync_package_to_stripe(package_type: PackageType) -> None:
        """
        Creates or updates the PackageType as a Product and recurring Price on the
        Platform Stripe Account, tagging it with the tenant_id in metadata.
        """
        tenant = package_type.tenant
        if not tenant:
            logger.warning(f"PackageType {package_type.id} is missing a tenant.")
            return

        product_id = package_type.stripe_product_id
        price_id = package_type.stripe_price_id

        interval_map = {
            'weekly': 'week',
            'monthly': 'month',
            'yearly': 'year'
        }
        stripe_interval = interval_map.get(package_type.billing_cycle, 'month')

        desc = f"{package_type.credit_count} credits | Valid for {package_type.validity_days} days | Billing: {package_type.billing_cycle.title()}"

        try: 
            if not product_id: 
                product = stripe.Product.create(
                    name=package_type.name,
                    description=desc,
                    active=package_type.is_active,
                    metadata={
                        "tenant_id": str(tenant.id),
                        "package_type_id": str(package_type.id)
                    }
                )
                product_id = product.id
                package_type.stripe_product_id = product_id
                logger.info(f"Created Stripe Product {product_id} for PackageType {package_type.id}")
            else: 
                stripe.Product.modify(
                    product_id,
                    name=package_type.name,
                    description=desc,
                    active=package_type.is_active,
                    metadata={
                        "tenant_id": str(tenant.id),
                        "package_type_id": str(package_type.id)
                    }
                )
                logger.info(f"Updated Stripe Product {product_id} for PackageType {package_type.id}")
 
            amount_cents = int(package_type.price * 100)
            
            should_create_price = False
            if not price_id:
                should_create_price = True
            else:
                try:
                    existing_price = stripe.Price.retrieve(price_id)
                    if existing_price.unit_amount != amount_cents or not existing_price.active or existing_price.recurring.get('interval') != stripe_interval:
                       
                        stripe.Price.modify(price_id, active=False)
                        should_create_price = True
                        logger.info(f"Price amount/interval changed or inactive. Deactivated price {price_id} on Stripe.")
                except stripe.error.StripeError as e:
                    logger.error(f"Error retrieving Stripe Price {price_id}: {str(e)}")
                    should_create_price = True

            if should_create_price:
                new_price = stripe.Price.create(
                    product=product_id,
                    unit_amount=amount_cents,
                    currency="usd",
                    recurring={"interval": stripe_interval},
                    metadata={
                        "tenant_id": str(tenant.id),
                        "package_type_id": str(package_type.id)
                    }
                )
                price_id = new_price.id
                package_type.stripe_price_id = price_id
                logger.info(f"Created new Stripe Price {price_id} for Product {product_id}")
 
            package_type.save(update_fields=['stripe_product_id', 'stripe_price_id'])

        except stripe.error.StripeError as e:
            logger.error(f"Stripe error syncing PackageType {package_type.id}: {str(e)}")
            pass
        except Exception as e:
            logger.error(f"Unexpected error syncing PackageType {package_type.id}: {str(e)}")
            pass

    @staticmethod
    def archive_package_on_stripe(package_type: PackageType) -> None:
        """
        Deactivates the Product and Price on Stripe so no further billing can occur.
        """
        product_id = package_type.stripe_product_id
        price_id = package_type.stripe_price_id

        try:
            if price_id:
                stripe.Price.modify(price_id, active=False)
                logger.info(f"Deactivated Price {price_id} on Stripe")
            if product_id:
                stripe.Product.modify(product_id, active=False)
                logger.info(f"Deactivated Product {product_id} on Stripe")
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error archiving PackageType {package_type.id}: {str(e)}")
            pass
        except Exception as e:
            logger.error(f"Unexpected error archiving PackageType {package_type.id}: {str(e)}")
            pass

    @staticmethod
    def handle_checkout_session_completed(session) -> None:
        """
        Handles successful package purchase checkout session.
        Creates the local Package and Payment records.
        """
        if not isinstance(session, dict):
            session = session.to_dict()

        metadata = session.get('metadata', {})
        package_type_id = metadata.get('package_type_id')
        client_id = metadata.get('client_id')
        tenant_id = metadata.get('tenant_id')

        if not package_type_id or not client_id:
            logger.warning("Checkout session missing package_type_id or client_id in metadata.")
            return

        from apps.scheduling.models import PackageType, Package, Payment
        from apps.users.models import User

        with bypass_tenant_isolation():
            try:
                package_type = PackageType.objects.get(id=package_type_id)
                client = User.objects.get(id=client_id)
            except (PackageType.DoesNotExist, User.DoesNotExist) as e:
                logger.error(f"PackageType or Client not found: {str(e)}")
                return

            subscription_id = session.get('subscription')
            if not subscription_id:
                logger.warning(f"Checkout session {session.get('id')} has no subscription ID.")
                return

            # Check if we already created it (idempotency)
            if Package.objects.filter(stripe_subscription_id=subscription_id).exists():
                logger.info(f"Package for subscription {subscription_id} already exists.")
                return

            from django.utils import timezone
            from datetime import timedelta

            # Create Package
            package = Package.objects.create(
                tenant_id=tenant_id,
                client=client,
                package_type=package_type,
                credits_remaining=package_type.credit_count,
                expires_at=timezone.now() + timedelta(days=package_type.validity_days),
                stripe_subscription_id=subscription_id,
                status='active'
            )

            # Create Payment record
            amount_total = session.get('amount_total', 0)
            payment = Payment.objects.create(
                tenant_id=tenant_id,
                client=client,
                amount=amount_total / 100.0,
                type='package_purchase',
                package=package,
                package_type=package_type,
                status='completed',
                provider_ref=session.get('id'),
                idempotency_key=session.get('id')
            )
            logger.info(f"Created Package {package.id} and Payment for checkout session {session.get('id')}")

            try:
                from apps.retention.tasks import recalculate_single_client_metrics, attribute_conversion_task
                recalculate_single_client_metrics.delay(str(tenant_id), str(client_id))
                attribute_conversion_task.delay(str(tenant_id), str(client_id), 'package_purchase', str(payment.id))
            except Exception:
                pass

    @staticmethod
    def handle_invoice_payment_succeeded(invoice) -> None:
        """
        Handles successful invoice payment for packages (especially recurring monthly renewals).
        Refills client credits, advances expiration date, and records the renewal payment.
        """
        if not isinstance(invoice, dict):
            invoice = invoice.to_dict() if hasattr(invoice, 'to_dict') else dict(invoice)

        subscription_id = invoice.get('subscription')
        if not subscription_id:
            return

        invoice_id = invoice.get('id')
        billing_reason = invoice.get('billing_reason') 

        from apps.scheduling.models import Package, Payment
        from django.utils import timezone
        from datetime import timedelta, datetime, timezone as dt_timezone
        from decimal import Decimal

        with bypass_tenant_isolation():
            try:
                package = Package.objects.select_related('package_type', 'client', 'tenant').get(
                    stripe_subscription_id=subscription_id
                )
            except Package.DoesNotExist:
                return

            pkg_type = package.package_type
            if not pkg_type:
                logger.warning(f"Package {package.id} has no package_type associated.")
                return
 
            if invoice_id and Payment.objects.filter(idempotency_key=invoice_id).exists():
                logger.info(f"Invoice {invoice_id} for package {package.id} already processed.")
                return
 
            if billing_reason == 'subscription_create':
                if package.status != 'active':
                    package.status = 'active'
                    package.save(update_fields=['status'])
                return
 
            now = timezone.now()
 
            lines = invoice.get('lines', {}).get('data', [])
            period_end_timestamp = None
            if lines and isinstance(lines, list) and len(lines) > 0 and isinstance(lines[0], dict):
                period_end_timestamp = lines[0].get('period', {}).get('end')

            if period_end_timestamp:
                calculated_end = datetime.fromtimestamp(period_end_timestamp, tz=dt_timezone.utc)
                if calculated_end > now:
                    new_expires_at = calculated_end
                else:
                    new_expires_at = now + timedelta(days=pkg_type.validity_days)
            else:
                base_date = package.expires_at if package.expires_at and package.expires_at > now else now
                new_expires_at = base_date + timedelta(days=pkg_type.validity_days)
 
            package.credits_remaining = pkg_type.credit_count
            package.total_credits_allocated = pkg_type.credit_count
            package.expires_at = new_expires_at
            package.status = 'active'
            package.save(update_fields=['credits_remaining', 'total_credits_allocated', 'expires_at', 'status'])
            logger.info(f"Renewed Package {package.id} for client {package.client_id}: +{pkg_type.credit_count} credits, expires_at={new_expires_at}")
 
            amount_paid = invoice.get('amount_paid', 0)
            payment_amount = Decimal(str(round(amount_paid / 100.0, 2))) if amount_paid else Decimal(str(pkg_type.price or 0.0))
            idempotency_key = invoice_id or f"pkg_renew_{package.id}_{int(now.timestamp())}"

            payment = Payment.objects.create(
                tenant=package.tenant,
                client=package.client,
                amount=payment_amount,
                type='package_purchase',
                package=package,
                package_type=pkg_type,
                status='completed',
                provider_ref=invoice_id or subscription_id,
                idempotency_key=idempotency_key
            )
            logger.info(f"Created renewal Payment {payment.id} for package {package.id}")

            try:
                from apps.retention.tasks import recalculate_single_client_metrics, attribute_conversion_task
                recalculate_single_client_metrics.delay(str(package.tenant_id), str(package.client_id))
                attribute_conversion_task.delay(str(package.tenant_id), str(package.client_id), 'package_purchase', str(payment.id))
            except Exception:
                pass

    @staticmethod
    def handle_subscription_updated(subscription) -> None:
        """
        Handles package subscription renewals or status updates.
        """
        if not isinstance(subscription, dict):
            subscription = subscription.to_dict() if hasattr(subscription, 'to_dict') else dict(subscription)

        subscription_id = subscription.get('id')
        status = subscription.get('status')
        cancel_at_period_end = subscription.get('cancel_at_period_end', False)
        current_period_end = subscription.get('current_period_end')

        from apps.scheduling.models import Package, Payment
        from django.utils import timezone
        from datetime import datetime, timedelta, timezone as dt_timezone

        with bypass_tenant_isolation():
            try:
                package = Package.objects.select_related('package_type').get(stripe_subscription_id=subscription_id)
                new_status = 'active' if status == 'active' else 'past_due' if status in ['past_due', 'unpaid'] else 'canceled'
                update_fields = ['status', 'cancel_at_period_end']
                package.status = new_status
                package.cancel_at_period_end = cancel_at_period_end

                # Secondary renewal safeguard: if current_period_end advanced past current expires_at
                if current_period_end and status == 'active':
                    new_expires_at = datetime.fromtimestamp(current_period_end, tz=dt_timezone.utc)
                    if not package.expires_at or new_expires_at > package.expires_at:
                        package.expires_at = new_expires_at
                        update_fields.append('expires_at')
                        if package.package_type:
                            package.credits_remaining = package.package_type.credit_count
                            package.total_credits_allocated = package.package_type.credit_count
                            update_fields.extend(['credits_remaining', 'total_credits_allocated'])
                            logger.info(f"Safeguard renewal: Extended Package {package.id} to {new_expires_at} with {package.package_type.credit_count} credits")

                package.save(update_fields=update_fields)
                logger.info(f"Updated Package {package.id} status to {package.status}")
            except Package.DoesNotExist:
                pass

    @staticmethod
    def handle_subscription_deleted(subscription) -> None:
        """
        Handles package subscription cancellations.
        """
        if not isinstance(subscription, dict):
            subscription = subscription.to_dict()

        subscription_id = subscription.get('id')
        
        from apps.scheduling.models import Package
        with bypass_tenant_isolation():
            try:
                package = Package.objects.get(stripe_subscription_id=subscription_id)
                package.status = 'canceled'
                package.save(update_fields=['status'])
                logger.info(f"Canceled Package {package.id}")
            except Package.DoesNotExist:
                pass
