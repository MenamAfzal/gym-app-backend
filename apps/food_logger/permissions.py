from rest_framework import permissions

class IsOwnerOrStaffReadOnly(permissions.BasePermission):
    """
    Object-level permission to only allow owners of an object to edit it.
    Staff members can view (read-only) ANY user's medications.
    """
    def has_object_permission(self, request, view, obj):
        if request.user == obj.user:
            return True
            
        if request.user.is_staff:
            return request.method in permissions.SAFE_METHODS
            
        return False
