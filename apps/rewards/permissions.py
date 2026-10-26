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
        if user_role == UserRole.GYM_MANAGER:
            view_name = view.__class__.__name__.lower()
            resource = 'programs'
            if 'badge' in view_name:
                resource = 'badges'
            elif 'tier' in view_name:
                resource = 'tiers'
            elif 'analytics' in view_name:
                resource = 'analytics'
            elif 'referral' in view_name:
                resource = 'referrals'
            
            action = 'view' if request.method in permissions.SAFE_METHODS else 'edit' if request.method in ['PUT', 'PATCH'] else 'delete' if request.method == 'DELETE' else 'create'
            from apps.users.permission_service import PermissionService
            return PermissionService.has_permission(request.user, 'rewards', resource, action)

        return user_role in [
            UserRole.PLATFORM_ADMIN,
            UserRole.GYM_OWNER,
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
