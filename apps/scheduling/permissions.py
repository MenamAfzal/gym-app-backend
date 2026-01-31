from rest_framework import permissions
from apps.users.models import UserRole

class IsGymStaffOrOwner(permissions.BasePermission):
    """
    Allows access to Gym Owners, Managers, and Trainers.
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated or not request.user.tenant:
            return False
        return request.user.role in [UserRole.GYM_OWNER, UserRole.GYM_MANAGER, UserRole.TRAINER]

class IsOwnerOrManager(permissions.BasePermission):
    """
    Strictly for admin duties (creating sessions, pricing).
    """
    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        return request.user.role in [UserRole.GYM_OWNER, UserRole.GYM_MANAGER]

class IsAssignedClient(permissions.BasePermission):
    """
    For Booking: Checks if the requesting client is actually assigned to the 
    staff member leading the session.
    """
    def has_object_permission(self, request, view, obj):
        # obj is the Session or Booking
        user = request.user
        
        # Admins/Staff bypass this check
        if user.role in [UserRole.GYM_OWNER, UserRole.GYM_MANAGER, UserRole.TRAINER]:
            return True
            
        if hasattr(obj, 'session'): # It's a Booking
            session = obj.session
        else: # It's a Session
            session = obj
            
        if not session.staff:
            return True # If no staff assigned, maybe open to all? Check reqs.
            
        # Check Assignment
        return user.assigned_staff_relations.filter(staff=session.staff).exists()
    