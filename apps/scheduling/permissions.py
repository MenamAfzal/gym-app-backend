from rest_framework import permissions
from apps.users.models import UserRole

class IsAuthenticated(permissions.BasePermission):
    """
    Allows access only to authenticated users.
    """
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated


class IsOwnerOrManager(permissions.BasePermission):
    """
    Strictly for admin duties (creating class templates, recurrence rules, rooms).
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.role in [UserRole.GYM_OWNER, UserRole.GYM_MANAGER]


class IsGymStaffOrOwner(permissions.BasePermission):
    """
    Allows access to Gym Owners, Managers, Trainers, Front Desk staff, and system staff/superusers.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_staff or request.user.is_superuser:
            return True
        return getattr(request.user, "role", None) in [
            UserRole.GYM_OWNER, 
            UserRole.GYM_MANAGER, 
            UserRole.TRAINER, 
            UserRole.FRONT_DESK
        ]



class IsFrontDeskOrAdmin(permissions.BasePermission):
    """
    Allows access to Gym Owners, Managers, and Front Desk staff (for check-ins, reporting).
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.role in [UserRole.GYM_OWNER, UserRole.GYM_MANAGER, UserRole.FRONT_DESK]


class IsInstructor(permissions.BasePermission):
    """
    Allows access only to Trainers (Instructors).
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.role == UserRole.TRAINER


class IsClient(permissions.BasePermission):
    """
    Allows access only to clients.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.role == UserRole.CLIENT


class IsAssignedClient(permissions.BasePermission):
    """
    For Booking: Checks if the requesting client is actually assigned to the 
    staff member leading the session (if constraint is active).
    """
    def has_object_permission(self, request, view, obj):
        user = request.user
        
        # Staff bypass this check
        if user.role in [UserRole.GYM_OWNER, UserRole.GYM_MANAGER, UserRole.TRAINER, UserRole.FRONT_DESK]:
            return True
            
        if hasattr(obj, 'session'): # It's a Booking
            session = obj.session
        else: # It's a ClassSession
            session = obj
            
        if not session.staff:
            return True 
            
        return user.assigned_staff_relations.filter(staff=session.staff).exists()