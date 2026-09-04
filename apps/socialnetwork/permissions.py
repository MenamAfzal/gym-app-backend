from rest_framework import permissions
from apps.users.models import UserRole


def is_admin_user(user):
    """
    Check if the user has admin/moderation privileges.
    Gym Owners, Gym Managers, Platform Admins, and staff/superusers have admin access.
    """
    if not user or not user.is_authenticated:
        return False
    if getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False):
        return True
    return getattr(user, 'role', None) in [
        UserRole.PLATFORM_ADMIN,
        UserRole.GYM_OWNER,
        UserRole.GYM_MANAGER,
    ]


class IsAuthenticated(permissions.BasePermission):
    """
    Allows access only to authenticated users.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class IsOwnerOrAdmin(permissions.BasePermission):
    """
    Object-level permission:
    - Author/Owner can edit or delete their own content.
    - Gym Admins (Gym Owner, Gym Manager, Platform Admin, staff) can moderate and delete any content in their gym.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

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
