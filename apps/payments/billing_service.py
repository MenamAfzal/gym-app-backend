import logging
from typing import Optional
import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from apps.core.tenants.models import Tenant
from .models import (
    BillingFeature,
    BillingPlan,
    TenantBillingSubscription,
)

logger = logging.getLogger(__name__)
stripe.api_key = settings.STRIPE_SECRET_KEY


class BillingValidationError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class FeatureBillingService:
    @classmethod
    def _validate_feature_selection(
        cls,
        plan: BillingPlan,
        feature_ids: list,
        all_features: list,
    ) -> "list[BillingFeature]":
        slug = plan.slug

        if slug == BillingPlan.PlanSlug.PREMIUM:
            return list(all_features)

        if slug == BillingPlan.PlanSlug.FREE:
            if feature_ids:
                raise BillingValidationError(
                    "The Free plan does not include any premium features."
                )
            return []

        if not feature_ids:
            raise BillingValidationError(
                f"You must select at least one feature for the {plan.name} plan."
            )

        active_feature_map = {str(f.id): f for f in all_features}
        resolved: "list[BillingFeature]" = []
        unknown_ids: "list[str]" = []

        for fid in feature_ids:
            fid_str = str(fid)
            if fid_str in active_feature_map:
                resolved.append(active_feature_map[fid_str])
            else:
                unknown_ids.append(fid_str)

        if unknown_ids:
            raise BillingValidationError(
                f"The following feature IDs are invalid or inactive: {', '.join(unknown_ids)}"
            )

        if slug == BillingPlan.PlanSlug.BASIC:
            if len(resolved) != 3:
                raise BillingValidationError(
                    f"The Basic plan requires exactly 3 features. You selected {len(resolved)}."
                )
            return resolved

        if slug == BillingPlan.PlanSlug.CUSTOM:
            return resolved

        raise BillingValidationError(f"Unknown plan slug: {slug}")

    @classmethod
    def create_checkout_session(
        cls,
        tenant: Tenant,
        plan_slug: str,
        feature_ids: list,
        customer_email: Optional[str] = None,
    ) -> dict:
        try:
            plan = BillingPlan.objects.get(slug=plan_slug)
        except BillingPlan.DoesNotExist:
            raise BillingValidationError(
                f"'{plan_slug}' is not a valid plan. Choose from: free, basic, premium, custom."
            )

        if plan.slug == BillingPlan.PlanSlug.FREE:
            raise BillingValidationError(
                "The Free plan is assigned automatically and requires no checkout."
            )

        active_features = list(BillingFeature.objects.filter(is_active=True))
        if not active_features:
            raise BillingValidationError(
                "No active premium features are currently available."
            )

        selected_features = cls._validate_feature_selection(
            plan, feature_ids, active_features
        )

        billing_cycles = {f.billing_cycle for f in selected_features}
        if len(billing_cycles) > 1:
            raise BillingValidationError(
                "You cannot purchase monthly and weekly features in the same transaction. "
                "Please checkout weekly and monthly features separately."
            )

        line_items = [
            {
                "price": feature.stripe_price_id,
                "quantity": 1,
            }
            for feature in selected_features
        ]

        selected_feature_ids = [str(f.id) for f in selected_features]
        metadata = {
            "tenant_id": str(tenant.id),
            "plan_slug": plan.slug,
            "feature_ids": ",".join(selected_feature_ids),
        }

        checkout_kwargs: dict = {
            "mode": "subscription",
            "line_items": line_items,
            "metadata": metadata,
            "success_url": (
                f"{settings.FRONTEND_URL}/billing/success"
                "?session_id={CHECKOUT_SESSION_ID}"
            ),
            "cancel_url": f"{settings.FRONTEND_URL}/billing/cancel",
            "subscription_data": {
                "metadata": metadata,
            },
        }

        if tenant.stripe_customer_id:
            checkout_kwargs["customer"] = tenant.stripe_customer_id
        elif customer_email:
            checkout_kwargs["customer_email"] = customer_email

        try:
            session = stripe.checkout.Session.create(**checkout_kwargs)
        except stripe.error.StripeError as exc:
            logger.error(
                "Stripe checkout session creation failed for tenant %s: %s",
                tenant.id,
                str(exc),
            )
            raise

        with transaction.atomic():
            TenantBillingSubscription.all_objects.filter(
                tenant=tenant,
                status=TenantBillingSubscription.StatusChoices.INCOMPLETE,
            ).delete()

            pending_sub = TenantBillingSubscription.objects.create(
                tenant=tenant,
                billing_plan=plan,
                stripe_checkout_session_id=session.id,
                status=TenantBillingSubscription.StatusChoices.INCOMPLETE,
            )
            pending_sub.active_features.set(selected_features)

        logger.info(
            "Created Stripe Checkout Session %s for tenant %s",
            session.id,
            tenant.id,
        )

        return {
            "checkout_url": session.url,
            "session_id": session.id,
        }

    @classmethod
    @transaction.atomic
    def fulfill_checkout(cls, session_obj: dict) -> TenantBillingSubscription:
        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            if hasattr(obj, key):
                return getattr(obj, key)
            try:
                return obj[key]
            except (KeyError, TypeError, Exception):
                pass
            return default
            
        metadata = _get(session_obj, "metadata", {})
        session_id = _get(session_obj, "id", "")
        stripe_subscription_id = _get(session_obj, "subscription")
        stripe_customer_id = _get(session_obj, "customer")

        tenant_id = _get(metadata, "tenant_id")
        plan_slug = _get(metadata, "plan_slug")
        feature_ids_raw = _get(metadata, "feature_ids", "")

        if not tenant_id or not plan_slug:
            raise ValueError(
                f"Webhook session {session_id} is missing required metadata."
            )

        feature_ids = [fid for fid in feature_ids_raw.split(",") if fid]

        logger.info(
            "Fulfilling checkout session %s: tenant=%s plan=%s",
            session_id,
            tenant_id,
            plan_slug,
        )

        try:
            tenant = Tenant.objects.get(id=tenant_id)
        except Tenant.DoesNotExist:
            raise ValueError(
                f"Tenant {tenant_id} not found."
            )

        try:
            billing_plan = BillingPlan.objects.get(slug=plan_slug)
        except BillingPlan.DoesNotExist:
            raise ValueError(
                f"BillingPlan '{plan_slug}' not found."
            )

        if feature_ids:
            features = list(BillingFeature.objects.filter(id__in=feature_ids))
            if len(features) != len(feature_ids):
                found_ids = {str(f.id) for f in features}
                missing = [fid for fid in feature_ids if fid not in found_ids]
                logger.warning(
                    "Some feature IDs from webhook metadata were not found: %s",
                    missing,
                )
        else:
            features = list(BillingFeature.objects.filter(is_active=True))

        try:
            billing_sub = TenantBillingSubscription.all_objects.select_for_update().get(
                tenant=tenant,
                stripe_checkout_session_id=session_id,
            )
        except TenantBillingSubscription.DoesNotExist:
            logger.warning(
                "No pending TenantBillingSubscription found for session %s. Creating new.",
                session_id,
            )
            billing_sub = TenantBillingSubscription(
                tenant=tenant,
                stripe_checkout_session_id=session_id,
            )

        billing_sub.billing_plan = billing_plan
        billing_sub.stripe_subscription_id = stripe_subscription_id
        billing_sub.status = TenantBillingSubscription.StatusChoices.ACTIVE

        if stripe_subscription_id:
            try:
                import stripe
                stripe.api_key = settings.STRIPE_SECRET_KEY
                stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)
                current_period_end_ts = stripe_sub.get("current_period_end")
                if current_period_end_ts:
                    billing_sub.current_period_end = timezone.datetime.fromtimestamp(
                        current_period_end_ts, tz=timezone.utc
                    )
            except Exception as e:
                logger.error(
                    "Failed to fetch stripe subscription details for %s during fulfillment: %s",
                    stripe_subscription_id,
                    e
                )

        billing_sub.save()
        billing_sub.active_features.set(features)

        # Record the payment in the platform ledger
        amount_total = _get(session_obj, "amount_total")
        if amount_total:
            import decimal
            from apps.payments.models import PlatformLedger
            amount_decimal = decimal.Decimal(amount_total) / decimal.Decimal(100)
            if not PlatformLedger.all_objects.filter(transaction_id=session_id).exists():
                PlatformLedger.objects.create(
                    tenant=tenant,
                    transaction_id=session_id,
                    amount_gross=amount_decimal,
                    platform_fee=amount_decimal,
                    amount_net=decimal.Decimal("0.00"),
                    type=PlatformLedger.TransactionType.SUBSCRIPTION,
                    status=PlatformLedger.StatusChoices.PAID,
                    description=f"Tenant Subscription: {billing_plan.name}"
                )

        # IMPORTANT: Cancel overlapping subscriptions or old core plan subscription to prevent double billing
        new_feature_ids = set(str(f.id) for f in features)
        old_subs = TenantBillingSubscription.all_objects.filter(
            tenant=tenant,
            status=TenantBillingSubscription.StatusChoices.ACTIVE
        ).exclude(id=billing_sub.id)

        for old_sub in old_subs:
            old_features = old_sub.active_features.all()
            old_feature_ids = set(str(f.id) for f in old_features)
            
            should_cancel = False
            # Overlapping features mean they are re-purchasing/replacing them
            if new_feature_ids and old_feature_ids and (new_feature_ids & old_feature_ids):
                should_cancel = True
            # Plan changed and the old subscription has no features
            elif old_sub.billing_plan != billing_plan and not old_feature_ids:
                should_cancel = True
            # Plan is the same, and both have no features (duplicate plan-only subscription)
            elif not new_feature_ids and not old_feature_ids and old_sub.billing_plan == billing_plan:
                should_cancel = True

            if should_cancel:
                if old_sub.stripe_subscription_id and old_sub.stripe_subscription_id != stripe_subscription_id:
                    try:
                        stripe.Subscription.delete(old_sub.stripe_subscription_id)
                        logger.info("Canceled old Stripe subscription %s for tenant %s", old_sub.stripe_subscription_id, tenant.id)
                    except Exception as e:
                        logger.error("Failed to cancel old Stripe subscription %s: %s", old_sub.stripe_subscription_id, e)
                
                old_sub.status = TenantBillingSubscription.StatusChoices.CANCELED
                old_sub.save(update_fields=["status"])

        if stripe_customer_id and not tenant.stripe_customer_id:
            tenant.stripe_customer_id = stripe_customer_id
            tenant.save(update_fields=["stripe_customer_id"])

        logger.info(
            "Subscription upgraded: tenant=%s plan=%s",
            tenant.id,
            billing_plan.slug,
        )

        return billing_sub

    @classmethod
    def handle_subscription_updated(cls, subscription_obj: dict) -> None:
        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            if hasattr(obj, key):
                return getattr(obj, key)
            try:
                return obj[key]
            except (KeyError, TypeError, Exception):
                pass
            return default

        stripe_sub_id = _get(subscription_obj, "id")
        raw_status = _get(subscription_obj, "status")
        current_period_end_ts = _get(subscription_obj, "current_period_end")

        try:
            billing_sub = TenantBillingSubscription.all_objects.get(
                stripe_subscription_id=stripe_sub_id
            )
        except TenantBillingSubscription.DoesNotExist:
            logger.debug(
                "No TenantBillingSubscription for Stripe sub %s.",
                stripe_sub_id,
            )
            return

        status_map = {
            "active": TenantBillingSubscription.StatusChoices.ACTIVE,
            "past_due": TenantBillingSubscription.StatusChoices.PAST_DUE,
            "canceled": TenantBillingSubscription.StatusChoices.CANCELED,
            "incomplete": TenantBillingSubscription.StatusChoices.INCOMPLETE,
            "incomplete_expired": TenantBillingSubscription.StatusChoices.CANCELED,
            "unpaid": TenantBillingSubscription.StatusChoices.PAST_DUE,
        }
        billing_sub.status = status_map.get(raw_status, billing_sub.status)

        if current_period_end_ts:
            billing_sub.current_period_end = timezone.datetime.fromtimestamp(
                current_period_end_ts, tz=timezone.utc
            )
            logger.info(
                "Set current_period_end for subscription %s: %s",
                stripe_sub_id,
                billing_sub.current_period_end,
            )
        else:
            logger.warning(
                "customer.subscription.updated for %s had no current_period_end.",
                stripe_sub_id,
            )

        billing_sub.save(update_fields=["status", "current_period_end"])
        logger.info("Synced subscription %s -> status=%s", stripe_sub_id, raw_status)

    @classmethod
    def handle_subscription_deleted(cls, subscription_obj: dict) -> None:
        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            if hasattr(obj, key):
                return getattr(obj, key)
            try:
                return obj[key]
            except (KeyError, TypeError, Exception):
                pass
            return default

        stripe_sub_id = _get(subscription_obj, "id")
        try:
            billing_sub = TenantBillingSubscription.all_objects.get(
                stripe_subscription_id=stripe_sub_id
            )
            billing_sub.status = TenantBillingSubscription.StatusChoices.CANCELED
            billing_sub.save(update_fields=["status"])
            logger.info("Subscription %s marked as canceled.", stripe_sub_id)
        except TenantBillingSubscription.DoesNotExist:
            logger.debug(
                "No TenantBillingSubscription for deleted Stripe sub %s.", stripe_sub_id
            )

    @classmethod
    def sync_feature_to_stripe(cls, feature: BillingFeature, old_price=None, old_cycle=None) -> None:
        """
        Creates or updates the product & price on Stripe, and saves IDs to DB.
        """
        # 1. Product creation / update
        if not feature.stripe_product_id:
            try:
                product = stripe.Product.create(
                    name=feature.name,
                    description=feature.description,
                    metadata={"code": feature.code, "type": "billing_feature"}
                )
                feature.stripe_product_id = product.id
            except Exception as e:
                logger.error(f"Failed to create Stripe product for {feature.name}: {e}")
                raise
        else:
            try:
                stripe.Product.modify(
                    feature.stripe_product_id,
                    name=feature.name,
                    description=feature.description
                )
            except Exception as e:
                logger.warning(f"Failed to modify Stripe product {feature.stripe_product_id}: {e}")

        # 2. Price creation / update
        # Stripe prices are immutable. If price value or billing cycle changes, or if stripe_price_id is missing, create a new one.
        price_changed = old_price is not None and feature.price != old_price
        cycle_changed = old_cycle is not None and feature.billing_cycle != old_cycle
        
        if not feature.stripe_price_id or price_changed or cycle_changed:
            try:
                # Deactivate the old price if it exists
                if feature.stripe_price_id:
                    try:
                        stripe.Price.modify(feature.stripe_price_id, active=False)
                    except Exception as old_err:
                        logger.warning(f"Could not deactivate old Stripe price {feature.stripe_price_id}: {old_err}")

                price_in_cents = int(feature.price * 100)
                interval_map = {'monthly': 'month', 'weekly': 'week'}
                stripe_interval = interval_map.get(feature.billing_cycle, 'month')
                price = stripe.Price.create(
                    product=feature.stripe_product_id,
                    unit_amount=price_in_cents,
                    currency="usd",
                    recurring={"interval": stripe_interval},
                    metadata={"code": feature.code}
                )
                feature.stripe_price_id = price.id
            except Exception as e:
                logger.error(f"Failed to create Stripe price for {feature.name}: {e}")
                raise

    @classmethod
    def delete_feature_from_stripe(cls, feature: BillingFeature) -> None:
        """
        Deactivates product and price on Stripe.
        """
        if feature.stripe_price_id:
            try:
                stripe.Price.modify(feature.stripe_price_id, active=False)
            except Exception as e:
                logger.warning(f"Could not deactivate Stripe price {feature.stripe_price_id}: {e}")
        if feature.stripe_product_id:
            try:
                stripe.Product.modify(feature.stripe_product_id, active=False)
            except Exception as e:
                logger.warning(f"Could not deactivate Stripe product {feature.stripe_product_id}: {e}")

