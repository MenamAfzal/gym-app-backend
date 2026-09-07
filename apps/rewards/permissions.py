"""
Reward Engine Permissions
"""
from rest_framework import permissions
from apps.users.models import UserRole


class IsRewardAdminOrManager(permissions.BasePermission):
    """
    Allows access to Platform Admins, Gym Owners, Gym Managers, and superusers.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser or getattr(request.user, 'is_staff', False):
            return True
        user_role = getattr(request.user, 'role', '')
        return user_role in [
            UserRole.PLATFORM_ADMIN,
            UserRole.GYM_OWNER,
            UserRole.GYM_MANAGER,
            'gym_admin',
            'admin',
            'owner'
        ]


class IsRewardStaffOrAdmin(permissions.BasePermission):
    """
    Allows access to Platform Admins, Gym Owners, Gym Managers, Front Desk Staff, and superusers/staff.
    Ensures Gym Admin / Gym Owner / Gym Manager can do everything a front-desk staff/manager can do.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser or getattr(request.user, 'is_staff', False):
            return True
        user_role = getattr(request.user, 'role', '')
        return user_role in [
            UserRole.PLATFORM_ADMIN,
            UserRole.GYM_OWNER,
            UserRole.GYM_MANAGER,
            UserRole.FRONT_DESK,
            'gym_admin',
            'admin',
            'owner',
            'front_desk_manager'
        ]


class IsRewardClient(permissions.BasePermission):
    """
    Allows access to authenticated clients within their tenant context.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)
