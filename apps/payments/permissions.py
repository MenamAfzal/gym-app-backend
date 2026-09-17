"""
Payments and Feature Entitlement Permissions.
"""
from rest_framework.permissions import BasePermission
from apps.payments.models import GymFeatureEntitlement


class GymFeaturePermission(BasePermission):
    """
    Permission class checking if the gym (tenant) has an active entitlement to a feature.
    Decoupled from global BillingFeature.is_active status so that existing gyms retain access
    during their active subscription cycle even when platform admins disable the feature globally.
    """
    feature_code = None

    def has_permission(self, request, view):
        feature_code = getattr(self, "feature_code", None) or getattr(view, "feature_code", None)
        if not feature_code:
            raise ValueError(f"{self.__class__.__name__} must define 'feature_code'")

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return False

        return GymFeatureEntitlement.is_gym_entitled(tenant, feature_code)
