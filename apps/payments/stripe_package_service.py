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

        try: 
            if not product_id: 
                product = stripe.Product.create(
                    name=package_type.name,
                    description=f"{package_type.credit_count} credits package",
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
    def handle_checkout_session_completed(session: dict) -> None:
        """
        Handles successful package purchase checkout session.
        Creates the local Package and Payment records.
        """
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
            Payment.objects.create(
                tenant_id=tenant_id,
                client=client,
                amount=amount_total / 100.0,
                type='package_purchase',
                status='completed',
                provider_ref=session.get('id'),
                idempotency_key=session.get('id')
            )
            logger.info(f"Created Package {package.id} and Payment for checkout session {session.get('id')}")

    @staticmethod
    def handle_subscription_updated(subscription: dict) -> None:
        """
        Handles package subscription renewals or status updates.
        """
        subscription_id = subscription.get('id')
        status = subscription.get('status')
        cancel_at_period_end = subscription.get('cancel_at_period_end', False)

        from apps.scheduling.models import Package, Payment
        
        with bypass_tenant_isolation():
            try:
                package = Package.objects.get(stripe_subscription_id=subscription_id)
                package.status = 'active' if status == 'active' else 'past_due' if status in ['past_due', 'unpaid'] else 'canceled'
                package.cancel_at_period_end = cancel_at_period_end
                package.save(update_fields=['status', 'cancel_at_period_end'])
                logger.info(f"Updated Package {package.id} status to {package.status}")

                # If this is a renewal (a new invoice was paid), we need to top up credits.
                # However, invoice.payment_succeeded is better for that. We'll handle basic status here.
            except Package.DoesNotExist:
                pass

    @staticmethod
    def handle_subscription_deleted(subscription: dict) -> None:
        """
        Handles package subscription cancellations.
        """
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
