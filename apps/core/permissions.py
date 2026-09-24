"""
Core Permissions

Base permission classes for tenant-aware access control in DRF views.
"""
from rest_framework.permissions import BasePermission
from apps.core.tenants.services import TenantEntitlementService


class TenantFeaturePermission(BasePermission):
    """
    Base permission class to check if tenant has access to a feature.
    
    Usage:
        class MyView(APIView):
            permission_classes = [CustomFeaturePermission]
            
        class CustomFeaturePermission(TenantFeaturePermission):
            feature_key = 'api_access'
    
    Subclasses must set:
        - feature_key: The feature key to check
    """
    
    feature_key = None  # Override in subclass
    
    def has_permission(self, request, view):
        """
        Check if request.tenant has the required feature.
        
        Args:
            request: DRF request with request.tenant set by middleware
            view: View instance
            
        Returns:
            bool: True if tenant has feature, False otherwise
        """
        if not self.feature_key:
            raise ValueError(
                f"{self.__class__.__name__} must define 'feature_key'"
            )
        
        if not hasattr(request, 'tenant'):
            # Middleware not applied or request not tenant-scoped
            return False
        
        return TenantEntitlementService.has_feature(
            request.tenant, 
            self.feature_key
        )


class TenantLimitPermission(BasePermission):
    """
    Base permission class to check if tenant is within a numeric limit.
    
    Usage:
        class MyView(APIView):
            permission_classes = [CustomLimitPermission]
            
        class CustomLimitPermission(TenantLimitPermission):
            feature_key = 'max_members'
            
            def get_current_count(self, request):
                return Member.objects.filter(tenant=request.tenant).count()
    
    Subclasses must set:
        - feature_key: The feature key for the limit
        - get_current_count(request): Method returning current count
    """
    
    feature_key = None  # Override in subclass
    
    def get_current_count(self, request):
        """
        Get current count of resource for tenant.
        Override this in subclass.
        
        Args:
            request: DRF request
            
        Returns:
            int: Current count
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement 'get_current_count'"
        )
    
    def has_permission(self, request, view):
        """
        Check if tenant is within limit.
        
        Args:
            request: DRF request with request.tenant set by middleware
            view: View instance
            
        Returns:
            bool: True if within limit, False otherwise
        """
        if not self.feature_key:
            raise ValueError(
                f"{self.__class__.__name__} must define 'feature_key'"
            )
        
        if not hasattr(request, 'tenant'):
            return False
        
        try:
            limit = TenantEntitlementService.get_limit(
                request.tenant,
                self.feature_key
            )
            current = self.get_current_count(request)
            return current < limit
        except ValueError:
            return False


class IsGymOwnerOnly(BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff:
            return True
        return getattr(user, 'role', None) in ['gym_owner', 'platform_admin']


class HasAppPermission(BasePermission):
    app_label = None
    resource_name = None
    required_action = None

    def __init__(self, app_label=None, resource_name=None, required_action=None):
        if app_label:
            self.app_label = app_label
        if resource_name:
            self.resource_name = resource_name
        if required_action:
            self.required_action = required_action

    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser or user.is_staff or getattr(user, 'role', None) in ['gym_owner', 'platform_admin']:
            return True

        if getattr(user, 'role', None) != 'gym_manager':
            return False

        app = self.app_label or getattr(view, 'permission_app', None)
        resource = self.resource_name or getattr(view, 'permission_resource', None)
        action = self.required_action or self._resolve_action(request, view)

        if not app or not resource or not action:
            return False

        from apps.users.permission_service import PermissionService
        return PermissionService.has_permission(user, app, resource, action)

    def _resolve_action(self, request, view):
        view_action = getattr(view, 'action', None)
        if view_action:
            action_map = {
                'list': 'view',
                'retrieve': 'view',
                'create': 'create',
                'update': 'edit',
                'partial_update': 'edit',
                'destroy': 'delete',
            }
            return action_map.get(view_action, view_action)

        method_map = {
            'GET': 'view',
            'POST': 'create',
            'PUT': 'edit',
            'PATCH': 'edit',
            'DELETE': 'delete',
        }
        return method_map.get(request.method, 'view')

