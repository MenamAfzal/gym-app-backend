from rest_framework import permissions
from apps.users.models import UserRole


def is_admin_user(user):
    """
    Check if the user has admin/moderation privileges.
    Gym Owners, Platform Admins, and staff/superusers have admin access.
    Gym Managers only have moderation access if granted 'moderate' or 'edit' or 'delete' permission.
    """
    if not user or not user.is_authenticated:
        return False
    if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
        return True
    if getattr(user, 'role', None) in [UserRole.PLATFORM_ADMIN, UserRole.GYM_OWNER]:
        return True
    if getattr(user, 'role', None) == UserRole.GYM_MANAGER:
        from apps.users.permission_service import PermissionService
        return (
            PermissionService.has_permission(user, 'socialnetwork', 'posts', 'moderate')
            or PermissionService.has_permission(user, 'socialnetwork', 'posts', 'edit')
            or PermissionService.has_permission(user, 'socialnetwork', 'posts', 'delete')
        )
    return False


class IsAuthenticated(permissions.BasePermission):
    """
    Allows access only to authenticated users.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class HasCommunityPermission(permissions.BasePermission):
    """
    Ensures that Gym Managers adhere to socialnetwork module permissions.
    Clients, Trainers, and Owners are allowed access according to standard rules.
    """
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return True
        if user.is_superuser or user.is_staff or getattr(user, 'role', None) in [UserRole.PLATFORM_ADMIN, UserRole.GYM_OWNER]:
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
                'update': 'edit',
                'partial_update': 'edit',
                'destroy': 'delete',
            }
            required_action = action_map.get(action, 'view')
            return PermissionService.has_permission(user, 'socialnetwork', 'posts', required_action)
        return True


class IsOwnerOrAdmin(permissions.BasePermission):
    """
    Object-level permission:
    - Author/Owner can edit or delete their own content.
    - Gym Admins (Gym Owner, Platform Admin, authorized Gym Manager, staff) can moderate and delete any content in their gym.
    """
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False
        if getattr(user, 'role', None) == UserRole.GYM_MANAGER:
            from apps.users.permission_service import PermissionService
            action = getattr(view, 'action', None)
            if not action and hasattr(request, 'method'):
                action = 'view' if request.method in ['GET', 'HEAD', 'OPTIONS'] else 'create' if request.method == 'POST' else 'edit'
            action_map = {
                'list': 'view',
                'retrieve': 'view',
                'create': 'create',
                'update': 'edit',
                'partial_update': 'edit',
                'destroy': 'delete',
            }
            required_action = action_map.get(action, 'view')
            return PermissionService.has_permission(user, 'socialnetwork', 'posts', required_action)
        return True

    def has_object_permission(self, request, view, obj):
        user = request.user
        if not user or not user.is_authenticated:
            return False
 
        if is_admin_user(user):
            if getattr(user, 'role', None) == UserRole.PLATFORM_ADMIN or getattr(user, 'is_superuser', False):
                return True 
            obj_tenant_id = getattr(obj, 'tenant_id', None)
            user_tenant_id = getattr(user, 'tenant_id', None)
            if obj_tenant_id and user_tenant_id:
                return obj_tenant_id == user_tenant_id
            return True 
        return getattr(obj, 'user_id', None) == user.id


class IsAdminOrModerator(permissions.BasePermission):
    """
    Permission class allowing access only to users with admin/moderation privileges
    (Gym Owner, Platform Admin, and staff/superusers).
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and is_admin_user(request.user))

