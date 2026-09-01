"""
Reward Engine Permissions
"""
from rest_framework import permissions
from apps.users.models import UserRole


class IsRewardAdminOrManager(permissions.BasePermission):
    """
    Allows access only to Platform Admins, Gym Owners, or Gym Managers.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.role in [
            UserRole.PLATFORM_ADMIN,
            UserRole.GYM_OWNER,
            UserRole.GYM_MANAGER
        ]


class IsRewardStaffOrAdmin(permissions.BasePermission):
    """
    Allows access to Platform Admins, Gym Owners, Managers, and Front Desk Staff.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.role in [
            UserRole.PLATFORM_ADMIN,
            UserRole.GYM_OWNER,
            UserRole.GYM_MANAGER,
            UserRole.FRONT_DESK
        ]


class IsRewardClient(permissions.BasePermission):
    """
    Allows access to authenticated clients within their tenant context.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)
