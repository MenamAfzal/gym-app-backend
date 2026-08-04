"""
Feature Billing Service
=======================

Centralised business logic for the feature-based subscription billing system.
All Stripe API interactions and DB state transitions live here; views stay thin.

Public API
----------
FeatureBillingService.create_checkout_session(tenant, plan_slug, feature_ids)
    -> Returns a Stripe Checkout Session URL.

FeatureBillingService.fulfill_checkout(session_obj)
    -> Called by the webhook handler after a successful payment.
       Upgrades the tenant's TenantBillingSubscription in a single transaction.
"""

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
    """
    Raised when request data violates plan-level feature constraints.
    Carries a user-facing message and an optional HTTP status hint.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


class FeatureBillingService:
    """
    Encapsulates all Stripe Checkout + DB upgrade logic.

    Design decisions
    ----------------
    - The service never touches ``request`` objects -- callers pass plain data.
    - All DB writes are wrapped in ``transaction.atomic()`` to guarantee
      consistency even if Stripe succeeds but the DB write fails mid-way.
    - ``stripe_price_id`` is never exposed to the client layer.
    """

    # ------------------------------------------------------------------ #
    #  Plan constraint validation                                          #
    # ------------------------------------------------------------------ #

    @classmethod
    def _validate_feature_selection(
        cls,
        plan: BillingPlan,
        feature_ids: list,
        all_features: list,
    ) -> "list[BillingFeature]":
        """
        Validate that the requested features satisfy the plan's constraints.

        Rules
        -----
        free     -> feature_ids must be empty (0 features).
        basic    -> exactly 3 features must be selected.
        premium  -> feature_ids is ignored; all active features are returned.
        custom   -> any non-empty selection is accepted; min 1 feature.

        Parameters
        ----------
        plan        : BillingPlan instance
        feature_ids : list of UUID strings sent by the client
        all_features: all active BillingFeature objects (pre-fetched)

        Returns
        -------
        List of resolved BillingFeature objects that should be charged.

        Raises
        ------
        BillingValidationError on any constraint violation.
        """
        slug = plan.slug

        # ---- Premium: override selection, return all active features ----
        if slug == BillingPlan.PlanSlug.PREMIUM:
            return list(all_features)

        # ---- Free: must have zero features ----
        if slug == BillingPlan.PlanSlug.FREE:
            if feature_ids:
                raise BillingValidationError(
                    "The Free plan does not include any premium features. "
                    "Please do not send feature IDs for a Free plan checkout."
                )
            return []

        # ---- Resolve the requested feature objects -------------------------
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
                f"The following feature IDs are invalid or inactive: "
                f"{', '.join(unknown_ids)}"
            )

        # ---- Basic: exactly 3 features ------------------------------------
        if slug == BillingPlan.PlanSlug.BASIC:
            if len(resolved) != 3:
                raise BillingValidationError(
                    f"The Basic plan requires exactly 3 features. "
                    f"You selected {len(resolved)}."
                )
            return resolved

        # ---- Custom: any count >= 1 ----------------------------------------
        if slug == BillingPlan.PlanSlug.CUSTOM:
            # Custom allows any feature combination -- no further restriction.
            return resolved

        raise BillingValidationError(f"Unknown plan slug: {slug}")

    # ------------------------------------------------------------------ #
    #  Stripe Checkout Session creation                                    #
    # ------------------------------------------------------------------ #

    @classmethod
    def create_checkout_session(
        cls,
        tenant: Tenant,
        plan_slug: str,
        feature_ids: list,
        customer_email: Optional[str] = None,
    ) -> dict:
        """
        Validate the request, build a Stripe Checkout Session, and persist
        a pending ``TenantBillingSubscription`` record.

        Parameters
        ----------
        tenant         : The Tenant (gym) initiating the upgrade.
        plan_slug      : One of 'free' | 'basic' | 'premium' | 'custom'.
        feature_ids    : List of BillingFeature UUIDs the gym owner selected.
        customer_email : Optional; used to pre-fill Stripe checkout email.

        Returns
        -------
        dict with keys:
            checkout_url : str   -- URL to redirect the frontend to.
            session_id   : str   -- Stripe cs_ ID for reference.

        Raises
        ------
        BillingValidationError   -- on invalid plan / feature combination.
        BillingPlan.DoesNotExist -- if the plan_slug is not seeded in DB.
        stripe.error.StripeError -- on any Stripe API failure.
        """
        # 1. Fetch plan and validate slug ----------------------------------------
        try:
            plan = BillingPlan.objects.get(slug=plan_slug)
        except BillingPlan.DoesNotExist:
            raise BillingValidationError(
                f"'{plan_slug}' is not a valid plan. "
                f"Choose from: free, basic, premium, custom."
            )

        # 2. Guard against downgrade via this endpoint (Free needs no Stripe) ----
        if plan.slug == BillingPlan.PlanSlug.FREE:
            raise BillingValidationError(
                "The Free plan is assigned automatically and requires no checkout."
            )

        # 3. Fetch all active billing features -----------------------------------
        active_features = list(BillingFeature.objects.filter(is_active=True))
        if not active_features:
            raise BillingValidationError(
                "No active premium features are currently available for purchase."
            )

        # 4. Validate feature selection against plan constraints -----------------
        selected_features = cls._validate_feature_selection(
            plan, feature_ids, active_features
        )

        # 5. Build Stripe line_items (one per feature) ---------------------------
        line_items = [
            {
                "price": feature.stripe_price_id,
                "quantity": 1,
            }
            for feature in selected_features
        ]

        # 6. Build metadata for webhook reconstruction --------------------------
        selected_feature_ids = [str(f.id) for f in selected_features]
        metadata = {
            "tenant_id": str(tenant.id),
            "plan_slug": plan.slug,
            # Stripe metadata values must be strings <= 500 chars
            "feature_ids": ",".join(selected_feature_ids),
        }

        # 7. Optionally reuse existing Stripe Customer ID -----------------------
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
                "metadata": metadata,  # also embedded in sub for future lookups
            },
        }

        if tenant.stripe_customer_id:
            checkout_kwargs["customer"] = tenant.stripe_customer_id
        elif customer_email:
            checkout_kwargs["customer_email"] = customer_email

        # 8. Call Stripe ---------------------------------------------------------
        try:
            session = stripe.checkout.Session.create(**checkout_kwargs)
        except stripe.error.StripeError as exc:
            logger.error(
                "Stripe checkout session creation failed for tenant %s: %s",
                tenant.id,
                str(exc),
            )
            raise  # Re-raise; view layer will catch and return 502.

        # 9. Persist a pending subscription record (pre-payment) ----------------
        with transaction.atomic():
            # Cancel any existing incomplete sessions for this tenant.
            # Use all_objects to ensure the delete works even if tenant
            # middleware context is not yet fully established.
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
            # Pre-populate M2M so we do not have to re-query features in webhook
            pending_sub.active_features.set(selected_features)

        logger.info(
            "Created Stripe Checkout Session %s for tenant %s (plan=%s, features=%s)",
            session.id,
            tenant.id,
            plan.slug,
            selected_feature_ids,
        )

        return {
            "checkout_url": session.url,
            "session_id": session.id,
        }

    # ------------------------------------------------------------------ #
    #  Webhook fulfillment                                                 #
    # ------------------------------------------------------------------ #

    @classmethod
    @transaction.atomic
    def fulfill_checkout(cls, session_obj: dict) -> TenantBillingSubscription:
        """
        Process a ``checkout.session.completed`` event payload and upgrade the
        tenant's subscription in the database.

        Steps
        -----
        1. Parse tenant_id, plan_slug, feature_ids from session metadata.
        2. Fetch the pending TenantBillingSubscription by checkout session ID.
        3. Fetch the resolved BillingFeature objects from the stored feature_ids.
        4. Atomically update the subscription: plan, Stripe IDs, status, features.
        5. Optionally store the Stripe Customer ID back on the Tenant.

        Parameters
        ----------
        session_obj : dict
            The ``data.object`` dict from the ``checkout.session.completed`` event.

        Returns
        -------
        The updated TenantBillingSubscription instance.

        Raises
        ------
        ValueError   -- if metadata is malformed or required objects are missing.
        """
        metadata = session_obj.get("metadata", {})
        session_id = session_obj.get("id", "")
        stripe_subscription_id = session_obj.get("subscription")
        stripe_customer_id = session_obj.get("customer")

        # -- 1. Parse metadata --------------------------------------------------
        tenant_id = metadata.get("tenant_id")
        plan_slug = metadata.get("plan_slug")
        feature_ids_raw = metadata.get("feature_ids", "")

        if not tenant_id or not plan_slug:
            raise ValueError(
                f"Webhook session {session_id} is missing required metadata "
                f"(tenant_id or plan_slug). Skipping fulfillment."
            )

        feature_ids = [fid for fid in feature_ids_raw.split(",") if fid]

        logger.info(
            "Fulfilling checkout session %s: tenant=%s plan=%s features=%s",
            session_id,
            tenant_id,
            plan_slug,
            feature_ids,
        )

        # -- 2. Resolve Tenant --------------------------------------------------
        try:
            tenant = Tenant.objects.get(id=tenant_id)
        except Tenant.DoesNotExist:
            raise ValueError(
                f"Tenant {tenant_id} not found during webhook fulfillment."
            )

        # -- 3. Resolve BillingPlan ---------------------------------------------
        try:
            billing_plan = BillingPlan.objects.get(slug=plan_slug)
        except BillingPlan.DoesNotExist:
            raise ValueError(
                f"BillingPlan '{plan_slug}' not found during webhook fulfillment."
            )

        # -- 4. Resolve features ------------------------------------------------
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
            # Premium plan -- grant all active features
            features = list(BillingFeature.objects.filter(is_active=True))

        # -- 5. Fetch or create the TenantBillingSubscription ------------------
        #    Prefer the pending record created during checkout initiation.
        #    IMPORTANT: Must use all_objects (bypass manager) here because the
        #    webhook handler runs with NO tenant in context — the standard
        #    `objects` manager would return queryset.none() and never find the
        #    pending record, silently creating a duplicate instead.
        try:
            billing_sub = TenantBillingSubscription.all_objects.select_for_update().get(
                tenant=tenant,
                stripe_checkout_session_id=session_id,
            )
        except TenantBillingSubscription.DoesNotExist:
            # Guard against replay / out-of-order events: create fresh record.
            logger.warning(
                "No pending TenantBillingSubscription found for session %s. "
                "Creating a new record.",
                session_id,
            )
            billing_sub = TenantBillingSubscription(
                tenant=tenant,
                stripe_checkout_session_id=session_id,
            )

        # -- 6. Upgrade the subscription ----------------------------------------
        billing_sub.billing_plan = billing_plan
        billing_sub.stripe_subscription_id = stripe_subscription_id
        billing_sub.status = TenantBillingSubscription.StatusChoices.ACTIVE
        billing_sub.save()

        # Update the M2M (replace any previously staged features)
        billing_sub.active_features.set(features)

        # -- 7. Persist Stripe Customer ID on the Tenant -----------------------
        if stripe_customer_id and not tenant.stripe_customer_id:
            tenant.stripe_customer_id = stripe_customer_id
            tenant.save(update_fields=["stripe_customer_id"])

        logger.info(
            "Subscription upgraded: tenant=%s plan=%s features=%s sub_id=%s",
            tenant.id,
            billing_plan.slug,
            [str(f.id) for f in features],
            stripe_subscription_id,
        )

        return billing_sub

    # ------------------------------------------------------------------ #
    #  Subscription lifecycle helpers (called by existing webhook handler) #
    # ------------------------------------------------------------------ #

    @classmethod
    def handle_subscription_updated(cls, subscription_obj: dict) -> None:
        """
        Sync status and period-end from a ``customer.subscription.updated`` event.
        Updates the TenantBillingSubscription if one exists.
        """
        stripe_sub_id = subscription_obj.get("id")
        raw_status = subscription_obj.get("status")
        current_period_end_ts = subscription_obj.get("current_period_end")

        try:
            # Use all_objects: webhook context has no tenant in scope
            billing_sub = TenantBillingSubscription.all_objects.get(
                stripe_subscription_id=stripe_sub_id
            )
        except TenantBillingSubscription.DoesNotExist:
            logger.debug(
                "No TenantBillingSubscription for Stripe sub %s -- skipping.",
                stripe_sub_id,
            )
            return

        # Map Stripe statuses to our choices
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

        billing_sub.save(update_fields=["status", "current_period_end"])
        logger.info("Synced subscription %s -> status=%s", stripe_sub_id, raw_status)

    @classmethod
    def handle_subscription_deleted(cls, subscription_obj: dict) -> None:
        """
        Mark a ``TenantBillingSubscription`` as canceled when Stripe fires
        ``customer.subscription.deleted``.
        """
        stripe_sub_id = subscription_obj.get("id")
        try:
            # Use all_objects: webhook context has no tenant in scope
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
