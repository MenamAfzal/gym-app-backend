from rest_framework import serializers
from .models import BillingFeature, BillingPlan, TenantBillingSubscription


class BillingFeatureSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingFeature
        fields = [
            "id",
            "name",
            "code",
            "description",
            "price",
            "billing_cycle",
            "stripe_product_id",
            "stripe_price_id",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "stripe_product_id", "stripe_price_id", "created_at", "updated_at"]


class BillingPlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = BillingPlan
        fields = ["id", "name", "slug", "allowed_feature_count", "is_public"]
        read_only_fields = fields


class TenantBillingSubscriptionSerializer(serializers.ModelSerializer):
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
            "cancel_at_period_end",
            "stripe_checkout_session_id",
            "stripe_subscription_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CheckoutRequestSerializer(serializers.Serializer):
    VALID_PLAN_SLUGS = [
        BillingPlan.PlanSlug.BASIC,
        BillingPlan.PlanSlug.PREMIUM,
        BillingPlan.PlanSlug.CUSTOM,
    ]

    plan = serializers.ChoiceField(choices=VALID_PLAN_SLUGS)
    feature_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        allow_empty=True,
    )

    def validate(self, attrs):
        plan_slug = attrs.get("plan")
        feature_ids = attrs.get("feature_ids", [])

        if plan_slug == BillingPlan.PlanSlug.BASIC and len(feature_ids) != 5:
            raise serializers.ValidationError(
                {
                    "feature_ids": (
                        f"The Basic plan requires exactly 5 feature IDs. "
                        f"You submitted {len(feature_ids)}."
                    )
                }
            )

        if plan_slug == BillingPlan.PlanSlug.CUSTOM and not feature_ids:
            raise serializers.ValidationError(
                {
                    "feature_ids": (
                        "The Custom plan requires at least 1 feature to be selected."
                    )
                }
            )

        attrs["feature_ids"] = [str(fid) for fid in feature_ids]
        return attrs

