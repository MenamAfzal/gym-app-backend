"""
Notification Engine — DRF Permissions

Permission classes for the notification engine API.
"""
from rest_framework import permissions
from apps.users.models import UserRole


NOTIFICATIONS_RESOURCE_MAP = {
    'notification-campaign': 'campaigns',
    'notification-template': 'templates',
    'notification-group': 'groups',
    'notification-automation': 'automations',
    'notification-settings': 'settings',
}

NOTIFICATIONS_ACTION_MAP = {
    'list': 'view',
    'retrieve': 'view',
    'create': 'create',
    'update': 'edit',
    'partial_update': 'edit',
    'destroy': 'delete',
    'send': 'send_campaign',
}


class IsOwnerOrManager(permissions.BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False
        if user.is_staff or user.is_superuser:
            return True
        if getattr(user, 'role', None) in [UserRole.GYM_OWNER, UserRole.PLATFORM_ADMIN]:
            return True
        if getattr(user, 'role', None) == UserRole.GYM_MANAGER:
            from apps.users.permission_service import PermissionService
            resource = getattr(view, 'permission_resource', None) or NOTIFICATIONS_RESOURCE_MAP.get(getattr(view, 'basename', ''), None)
            if not resource:
                view_name = view.__class__.__name__.lower()
                if 'setting' in view_name:
                    resource = 'settings'
                elif 'group' in view_name:
                    resource = 'groups'
                else:
                    resource = 'campaigns'
            action = getattr(view, 'action', None)
            if not action and hasattr(request, 'method'):
                action = 'list' if request.method in permissions.SAFE_METHODS else 'update' if request.method in ['PUT', 'PATCH'] else 'create'
            resolved_action = NOTIFICATIONS_ACTION_MAP.get(action, 'edit')
            return PermissionService.has_permission(user, 'notifications', resource, resolved_action)
        return False


class IsGymOwnerOnly(permissions.BasePermission):
    """
    Restricts access to Gym Owners only.
    Used for: automations, tenant notification settings.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return getattr(request.user, 'role', None) == UserRole.GYM_OWNER


class IsNotificationRecipient(permissions.BasePermission):
    """
    Object-level permission: only the recipient of a NotificationInbox item can access it.
    Used for: inbox mark-read operations.
    """
    def has_object_permission(self, request, view, obj):
        return obj.recipient_id == request.user.id


class IsOwnDevice(permissions.BasePermission):
    """
    Object-level permission: only the owner of an FCMDevice can delete it.
    """
    def has_object_permission(self, request, view, obj):
        return obj.user_id == request.user.id


class IsOwnPreference(permissions.BasePermission):
    """
    Object-level permission: only the user can access/update their own preferences.
    """
    def has_object_permission(self, request, view, obj):
        return obj.user_id == request.user.id
