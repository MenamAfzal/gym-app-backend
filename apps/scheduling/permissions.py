from rest_framework import permissions
from apps.users.models import UserRole

class IsAuthenticated(permissions.BasePermission):
    """
    Allows access only to authenticated users.
    """
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated


SCHEDULING_RESOURCE_MAP = {
    'location': 'locations',
    'locations': 'locations',
    'staff-location': 'locations',
    'class-template': 'classes',
    'recurrence-rule': 'classes',
    'session': 'sessions',
    'event': 'sessions',
    'workshop': 'events',
    'mindbody-event': 'events',
    'event-enrollment': 'events',
    'room': 'rooms',
    'layout': 'rooms',
    'spot-type': 'spot_types',
    'package-type': 'packages',
    'package': 'packages',
    'booking': 'bookings',
    'waitlist': 'waitlist',
    'substitute-request': 'substitutes',
    'substitutes': 'substitutes',
    'staff-availability': 'staff_availability',
    'appointment': 'appointments',
    'appointments': 'appointments',
    'facility-access': 'facility_access',
    'facility_access': 'facility_access',
    'staff-assignment': 'staff_assignments',
    'staff_assignment': 'staff_assignments',
    'staff-assignments': 'staff_assignments',
    'staff_assignments': 'staff_assignments',
    'reports': 'reports',
    'report': 'reports',
    'booking-settings': 'booking_settings',
    'settings': 'booking_settings',
}

SCHEDULING_ACTION_MAP = {
    'list': 'view',
    'retrieve': 'view',
    'create': 'create',
    'update': 'edit',
    'partial_update': 'edit',
    'destroy': 'delete',
    'cancel': 'cancel_session',
    'cancel_event': 'cancel_event',
    'cancel_session': 'cancel_session',
    'check_in': 'check_in',
    'check_out': 'check_out',
    'bulk_assign': 'create',
    'stats': 'view',
    'fill_rate': 'view',
    'roster': 'roster',
    'enroll': 'roster',
    'accept': 'edit',
    'upcoming': 'view',
    'my_enrollments': 'view',
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
            app = getattr(view, 'permission_app', None)
            resource = getattr(view, 'permission_resource', None)
            basename = getattr(view, 'basename', '')
            if not app:
                if basename in ['user', 'staff', 'users']:
                    app = 'staff_users'
                    resource = resource or 'staff'
                elif 'retention' in basename:
                    app = 'retention'
                    resource = resource or ('segments' if 'segment' in basename else 'metrics')
                else:
                    app = 'scheduling'
                    resource = resource or SCHEDULING_RESOURCE_MAP.get(basename)
            if not resource:
                return False
            action = getattr(view, 'action', None)
            if not action and hasattr(request, 'method'):
                action = 'view' if request.method in ['GET', 'HEAD', 'OPTIONS'] else 'edit'
            resolved_action = SCHEDULING_ACTION_MAP.get(action, action or 'view')
            return PermissionService.has_permission(user, app, resource, resolved_action)
        return False


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
    For Gym Managers, checks ManagerPermissionPolicy if app/resource configured.
    """
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
            basename = getattr(view, 'basename', '')
            view_name = view.__class__.__name__.lower()
            if 'report' in basename or 'report' in view_name:
                return PermissionService.has_permission(user, 'scheduling', 'reports', 'view')
            if 'facility' in basename or 'facility' in view_name:
                action = getattr(view, 'action', None)
                if not action and hasattr(request, 'method'):
                    action = 'view' if request.method in permissions.SAFE_METHODS else 'create'
                resolved_action = SCHEDULING_ACTION_MAP.get(action, 'view')
                return PermissionService.has_permission(user, 'scheduling', 'facility_access', resolved_action)
            return True
        return getattr(user, 'role', None) == UserRole.FRONT_DESK


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