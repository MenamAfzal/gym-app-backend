from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action
from .models import Ticket, TicketComment
from .serializers import TicketSerializer, TicketCommentSerializer
from apps.users.models import UserRole
from apps.core.tenants.context import bypass_tenant_isolation

class IsPlatformAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == UserRole.PLATFORM_ADMIN

class IsGymOwnerOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in [UserRole.GYM_OWNER, UserRole.PLATFORM_ADMIN]


class TenantTicketPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False
        if user.is_staff or user.is_superuser or getattr(user, 'role', None) in [UserRole.GYM_OWNER, UserRole.PLATFORM_ADMIN]:
            return True
        if getattr(user, 'role', None) == UserRole.GYM_MANAGER:
            from apps.users.permission_service import PermissionService
            action = getattr(view, 'action', None)
            if not action and hasattr(request, 'method'):
                action = 'view' if request.method in ['GET', 'HEAD', 'OPTIONS'] else 'create' if request.method == 'POST' else 'edit'
            action_map = {
                'list': 'view',
                'retrieve': 'view',
                'create': 'create',
                'add_comment': 'create',
                'update': 'edit',
                'partial_update': 'edit',
                'destroy': 'delete',
            }
            required_action = action_map.get(action, 'view')
            return PermissionService.has_permission(user, 'support', 'tickets', required_action)
        return False


class TenantTicketViewSet(viewsets.ModelViewSet):
    """
    For Gym Owners and authorized Managers to manage tickets for their specific tenant.
    """
    serializer_class = TicketSerializer
    permission_app = 'support'
    permission_resource = 'tickets'
    permission_classes = [TenantTicketPermission]

    def get_queryset(self):
        return Ticket.objects.all().select_related(
            'created_by__profile', 
            'assigned_to__profile'
        ).prefetch_related(
            'comments', 
            'comments__author__profile'
        )

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=['post'])
    def add_comment(self, request, pk=None):
        ticket = self.get_object()
        serializer = TicketCommentSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(ticket=ticket, author=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PlatformTicketViewSet(viewsets.ModelViewSet):
    """
    For Platform Admins to manage tickets across ALL tenants.
    """
    serializer_class = TicketSerializer
    permission_classes = [IsPlatformAdmin]

    def get_queryset(self):
        # Use bypass to see everything across all tenants
        with bypass_tenant_isolation():
            return Ticket.all_objects.all().select_related(
                'created_by__profile', 
                'assigned_to__profile'
            ).prefetch_related(
                'comments', 
                'comments__author__profile'
            )

    def list(self, request, *args, **kwargs):
        with bypass_tenant_isolation():
            return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        with bypass_tenant_isolation():
            return super().retrieve(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def add_comment(self, request, pk=None):
        with bypass_tenant_isolation():
            try:
                ticket = Ticket.all_objects.get(pk=pk)
            except Ticket.DoesNotExist:
                return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
            
            serializer = TicketCommentSerializer(data=request.data)
            if serializer.is_valid():
                # Manually set tenant context for the comment creation to match the ticket
                from apps.core.tenants.context import set_current_tenant, reset_current_tenant
                token = set_current_tenant(ticket.tenant)
                try:
                    serializer.save(ticket=ticket, author=request.user)
                finally:
                    reset_current_tenant(token)
                    
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
