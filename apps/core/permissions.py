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
            # Feature not found or not numeric
            return False
