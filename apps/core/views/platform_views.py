from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError

from apps.core.tenants.models import Tenant, Plan, Feature
from apps.core.tenants.services import TenantAdministrationService
from apps.core.tenants.serializers import (
    TenantSerializer, 
    PlanSerializer, 
    FeatureSerializer,
    OnboardTenantSerializer,
    TenantEntitlementOverrideSerializer
)

class IsPlatformAdmin(permissions.BasePermission):
    """
    Strict Permission: Only Platform Super Admins allowed.
    """
    def has_permission(self, request, view):
        return (
            request.user and 
            request.user.is_authenticated and 
            request.user.is_staff and 
            request.user.tenant is None
        )

class PlatformTenantViewSet(viewsets.ModelViewSet):
    """
    API for Managing Gyms (Tenants).
    Only accessible by Platform Admins.
    """
    queryset = Tenant.objects.all().prefetch_related('subscriptions').order_by('-created_at')
    serializer_class = TenantSerializer
    def get_permissions(self):
        """
        Allow anyone to list tenants (e.g. for a directory),
        but restrict creation/management to Platform Admins.
        """
        if self.action == 'list':
            return [permissions.AllowAny()]
        return [IsPlatformAdmin()]
    
    def create(self, request, *args, **kwargs):
        """
        Override create to use the Onboarding Service.
        """
        serializer = OnboardTenantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            tenant = TenantAdministrationService.onboard_tenant(
                gym_name=data['gym_name'],
                subdomain=data['subdomain'],
                owner_email=data['owner_email'],
                owner_password=data['owner_password'],
                initial_plan_id=data.get('initial_plan_id')
            )
            return Response(
                TenantSerializer(tenant).data, 
                status=status.HTTP_201_CREATED
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def assign_plan(self, request, pk=None):
        """
        Assign a plan to an existing tenant.
        Payload: { "plan_id": "uuid" }
        """
        tenant = self.get_object()
        plan_id = request.data.get('plan_id')
        
        if not plan_id:
            return Response({"detail": "plan_id is required"}, status=400)
            
        try:
            plan = Plan.objects.get(id=plan_id)
            TenantAdministrationService.assign_plan(tenant, plan)
            return Response({'status': 'Plan assigned successfully'})
        except Plan.DoesNotExist:
            return Response({"detail": "Plan not found"}, status=404)

    @action(detail=True, methods=['post'])
    def set_override(self, request, pk=None):
        """
        Set a feature override for this tenant.
        Payload: { "feature_id": "uuid", "value": true/false/100, "expires_at": null }
        """
        tenant = self.get_object()
        feature_id = request.data.get('feature_id')
        value = request.data.get('value')
        expires_at = request.data.get('expires_at') # Optional

        try:
            override = TenantAdministrationService.set_feature_override(
                tenant=tenant,
                feature_id=feature_id,
                value=value,
                expires_at=expires_at
            )
            return Response(
                TenantEntitlementOverrideSerializer(override).data,
                status=status.HTTP_200_OK
            )
        except Feature.DoesNotExist:
            return Response({"detail": "Feature not found"}, status=404)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=400)

class PlatformPlanViewSet(viewsets.ModelViewSet):
    """
    API for Managing Plans.
    """
    queryset = Plan.objects.all().order_by('price')
    serializer_class = PlanSerializer
    permission_classes = [IsPlatformAdmin]

class PlatformFeatureViewSet(viewsets.ModelViewSet):
    """
    API for Managing System Features.
    """
    queryset = Feature.objects.all().order_by('key')
    serializer_class = FeatureSerializer
    permission_classes = [IsPlatformAdmin]
    