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


class GymEntitledFeatureSerializer(serializers.ModelSerializer):
    """
    Serializes a BillingFeature in the context of a specific gym's subscription entitlement.
    - is_active: Reflects whether the gym's entitlement to this feature is currently active.
      Even if the Platform Admin deactivates the feature at the platform level, an existing
      gym retains access (is_active=True) until their subscription cycle ends or is canceled.
    - platform_is_active: The platform-level catalog flag (whether new gyms can purchase it).
    - is_entitled: Semantic boolean for gym access.
    - current_period_end: The subscription period end date for this entitlement.
    """
    is_active = serializers.SerializerMethodField()
    platform_is_active = serializers.BooleanField(source="is_active", read_only=True)
    is_entitled = serializers.SerializerMethodField()
    current_period_end = serializers.SerializerMethodField()

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
            "platform_is_active",
            "is_entitled",
            "current_period_end",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def _get_subscription(self):
        return self.context.get("subscription")

    def get_is_entitled(self, obj):
        subscription = self._get_subscription()
        if subscription:
            return subscription.is_entitled
        return obj.is_active

    def get_is_active(self, obj):
        return self.get_is_entitled(obj)

    def get_current_period_end(self, obj):
        subscription = self._get_subscription()
        if subscription and subscription.current_period_end:
            cpe = subscription.current_period_end
            if hasattr(cpe, "isoformat"):
                return cpe.isoformat()
            return str(cpe)
        return None


class TenantBillingSubscriptionSerializer(serializers.ModelSerializer):
    billing_plan = BillingPlanSerializer(read_only=True)
    active_features = serializers.SerializerMethodField()
    is_entitled = serializers.BooleanField(read_only=True)

    class Meta:
        model = TenantBillingSubscription
        fields = [
            "id",
            "billing_plan",
            "active_features",
            "status",
            "current_period_end",
            "cancel_at_period_end",
            "is_entitled",
            "stripe_checkout_session_id",
            "stripe_subscription_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_active_features(self, obj):
        features = obj.active_features.all()
        serializer = GymEntitledFeatureSerializer(
            features,
            many=True,
            context={"subscription": obj}
        )
        return serializer.data


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

