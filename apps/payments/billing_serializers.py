"""
Billing Serializers
===================

Serializers for the feature-based subscription billing system.

- BillingFeatureSerializer     : Read-only; safe for frontend (no stripe_price_id).
- BillingPlanSerializer        : Read-only; exposes plan metadata + constraints.
- TenantBillingSubscriptionSerializer : Read; includes nested features + plan.
- CheckoutRequestSerializer    : Write; validates checkout requests per plan rules.
"""

from rest_framework import serializers
from .models import BillingFeature, BillingPlan, TenantBillingSubscription


# ---------------------------------------------------------------------------
# Read serializers (safe for frontend consumption)
# ---------------------------------------------------------------------------

class BillingFeatureSerializer(serializers.ModelSerializer):
    """
    Exposes billing features for the upgrade/checkout UI.
    NOTE: stripe_price_id is intentionally excluded -- never expose to clients.
    """

    class Meta:
        model = BillingFeature
        fields = ["id", "name", "code", "description", "is_active"]
        read_only_fields = fields


class BillingPlanSerializer(serializers.ModelSerializer):
    """
    Exposes plan tiers and their feature-count constraints.
    """

    class Meta:
        model = BillingPlan
        fields = ["id", "name", "slug", "allowed_feature_count", "is_public"]
        read_only_fields = fields


class TenantBillingSubscriptionSerializer(serializers.ModelSerializer):
    """
    Full read representation of a tenant's active billing subscription.
    Includes nested plan + active features for dashboard display.
    """

    billing_plan = BillingPlanSerializer(read_only=True)
    active_features = BillingFeatureSerializer(many=True, read_only=True)

    class Meta:
        model = TenantBillingSubscription
        fields = [
            "id",
            "billing_plan",
            "active_features",
            "status",
            "current_period_end",
            "stripe_checkout_session_id",
            # NOTE: stripe_subscription_id is omitted -- internal only.
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Write serializer (request validation)
# ---------------------------------------------------------------------------

class CheckoutRequestSerializer(serializers.Serializer):
    """
    Validates the body of a POST /billing/checkout/ request.

    Fields
    ------
    plan        : str  -- One of 'basic' | 'premium' | 'custom'.
    feature_ids : list -- UUIDs of BillingFeature objects to purchase.
                         Required for Basic / Custom; ignored for Premium
                         (service layer overrides to all features).

    Validation
    ----------
    Cross-field validation (Basic = 3 features, Custom = any >= 1) is
    intentionally delegated to ``FeatureBillingService`` so the DB lookup
    (resolving UUIDs to objects) happens only once, in the service layer.
    Here we only do lightweight structural checks.
    """

    VALID_PLAN_SLUGS = [
        BillingPlan.PlanSlug.BASIC,
        BillingPlan.PlanSlug.PREMIUM,
        BillingPlan.PlanSlug.CUSTOM,
    ]

    plan = serializers.ChoiceField(
        choices=VALID_PLAN_SLUGS,
        help_text=(
            "Target plan slug. One of: 'basic', 'premium', 'custom'. "
            "The 'free' plan is assigned automatically and cannot be purchased."
        ),
    )
    feature_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        allow_empty=True,
        help_text=(
            "List of BillingFeature UUIDs to include in this subscription. "
            "Required for 'basic' (exactly 3) and 'custom' (any count >= 1). "
            "Ignored for 'premium' (all 5 features are auto-included)."
        ),
    )

    def validate(self, attrs):
        plan_slug = attrs.get("plan")
        feature_ids = attrs.get("feature_ids", [])

        # Structural pre-check: Basic must have >= 3 IDs submitted
        # (exact = 3 check is done in service with resolved objects)
        if plan_slug == BillingPlan.PlanSlug.BASIC and len(feature_ids) != 3:
            raise serializers.ValidationError(
                {
                    "feature_ids": (
                        f"The Basic plan requires exactly 3 feature IDs. "
                        f"You submitted {len(feature_ids)}."
                    )
                }
            )

        # Custom: must have at least 1 feature ID
        if plan_slug == BillingPlan.PlanSlug.CUSTOM and not feature_ids:
            raise serializers.ValidationError(
                {
                    "feature_ids": (
                        "The Custom plan requires at least 1 feature to be selected."
                    )
                }
            )

        # Convert UUIDs to strings for downstream use
        attrs["feature_ids"] = [str(fid) for fid in feature_ids]
        return attrs
