from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from .models import Product, StockTransaction
from .serializers import ProductSerializer, StockTransactionSerializer
from apps.users.models import UserRole
from apps.core.tenants.context import bypass_tenant_isolation

class IsStaffOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [UserRole.GYM_MANAGER, UserRole.FRONT_DESK, UserRole.GYM_OWNER, UserRole.PLATFORM_ADMIN]

class ProductViewSet(viewsets.ModelViewSet):
    """
    Manage products. Filtered by staff location automatically.
    """
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [IsStaffOrAdmin]

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset()
        if getattr(user, 'role', None) in [UserRole.GYM_MANAGER, UserRole.FRONT_DESK]:
            qs = qs.filter(location__stafflocation__staff=user).distinct()
        return qs

class StockTransactionViewSet(viewsets.ModelViewSet):
    """
    Append-only stock ledger.
    """
    queryset = StockTransaction.objects.all()
    serializer_class = StockTransactionSerializer
    permission_classes = [IsStaffOrAdmin]

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset()
        if getattr(user, 'role', None) in [UserRole.GYM_MANAGER, UserRole.FRONT_DESK]:
            qs = qs.filter(product__location__stafflocation__staff=user).distinct()
        return qs

    def perform_create(self, serializer):
        serializer.save(handled_by=self.request.user)
