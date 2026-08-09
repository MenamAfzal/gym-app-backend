"""
Notification Engine — DRF Permissions

Permission classes for the notification engine API.
"""
from rest_framework import permissions
from apps.users.models import UserRole


class IsOwnerOrManager(permissions.BasePermission):
    """
    Allows access to Gym Owners and Gym Managers.
    Used for: campaigns, templates, groups.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return getattr(request.user, 'role', None) in [
            UserRole.GYM_OWNER, UserRole.GYM_MANAGER
        ]


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
