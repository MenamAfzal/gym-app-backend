from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from .models import Product, StockTransaction
from .serializers import ProductSerializer, StockTransactionSerializer
from apps.users.models import UserRole
from apps.core.tenants.context import bypass_tenant_isolation


class ProductPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff or getattr(user, 'role', None) in [UserRole.GYM_OWNER, UserRole.PLATFORM_ADMIN]:
            return True
        if getattr(user, 'role', None) == UserRole.FRONT_DESK:
            return request.method in permissions.SAFE_METHODS
        if getattr(user, 'role', None) == UserRole.GYM_MANAGER:
            action = getattr(view, 'action', None)
            if not action and hasattr(request, 'method'):
                action = 'view' if request.method in permissions.SAFE_METHODS else 'create' if request.method == 'POST' else 'edit'
            action_map = {
                'list': 'view',
                'retrieve': 'view',
                'create': 'create',
                'update': 'edit',
                'partial_update': 'edit',
                'destroy': 'delete',
            }
            required_action = action_map.get(action, 'view')
            from apps.users.permission_service import PermissionService
            return PermissionService.has_permission(user, 'inventory', 'products', required_action)
        return False


class StockTransactionPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser or user.is_staff or getattr(user, 'role', None) in [UserRole.GYM_OWNER, UserRole.PLATFORM_ADMIN]:
            return True
        if getattr(user, 'role', None) == UserRole.FRONT_DESK:
            return True
        if getattr(user, 'role', None) == UserRole.GYM_MANAGER:
            action = getattr(view, 'action', None)
            if not action and hasattr(request, 'method'):
                action = 'view' if request.method in permissions.SAFE_METHODS else 'adjust_stock' if request.method == 'POST' else 'adjust_stock'
            action_map = {
                'list': 'view',
                'retrieve': 'view',
                'create': 'adjust_stock',
                'update': 'adjust_stock',
                'partial_update': 'adjust_stock',
                'destroy': 'adjust_stock',
            }
            required_action = action_map.get(action, 'view')
            from apps.users.permission_service import PermissionService
            return PermissionService.has_permission(user, 'inventory', 'stock', required_action)
        return False


class ProductViewSet(viewsets.ModelViewSet):
    """
    Manage products. Filtered by staff location automatically for Front Desk.
    """
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [ProductPermission]

    def get_queryset(self):
        user = self.request.user
        from django.db.models import Sum
        
        if getattr(user, 'role', None) == UserRole.PLATFORM_ADMIN:
            with bypass_tenant_isolation():
                return Product.all_objects.all().select_related('location', 'tenant').annotate(
                    annotated_stock=Sum('transactions__quantity')
                )
        
        qs = Product.objects.all().select_related('location').annotate(
            annotated_stock=Sum('transactions__quantity')
        )
        if getattr(user, 'role', None) == UserRole.FRONT_DESK:
            qs = qs.filter(location__location_staff__staff=user).distinct()
        return qs


class StockTransactionViewSet(viewsets.ModelViewSet):
    """
    Append-only stock ledger.
    """
    queryset = StockTransaction.objects.all()
    serializer_class = StockTransactionSerializer
    permission_classes = [StockTransactionPermission]

    def get_queryset(self):
        user = self.request.user
        if getattr(user, 'role', None) == UserRole.PLATFORM_ADMIN:
            with bypass_tenant_isolation():
                return StockTransaction.all_objects.all().select_related('product', 'handled_by__profile', 'tenant')
                
        qs = StockTransaction.objects.all().select_related('product', 'handled_by__profile')
        if getattr(user, 'role', None) == UserRole.FRONT_DESK:
            qs = qs.filter(product__location__location_staff__staff=user).distinct()
        return qs

    def perform_create(self, serializer):
        serializer.save(handled_by=self.request.user)


