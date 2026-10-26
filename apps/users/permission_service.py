from apps.users.models import User, UserRole, ManagerPermissionPolicy
from apps.core.permissions_catalog import sanitize_permissions, get_permission_catalog


class PermissionService:
    @classmethod
    def _get_policy(cls, user):
        if hasattr(user, '_cached_manager_policy') and user._cached_manager_policy is not None:
            return user._cached_manager_policy
        policy = ManagerPermissionPolicy.all_objects.filter(manager=user).first()
        user._cached_manager_policy = policy
        return policy

    @classmethod
    def has_permission(cls, user, app: str, resource: str, action: str) -> bool:
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser or user.is_staff or getattr(user, 'role', None) in [UserRole.PLATFORM_ADMIN, UserRole.GYM_OWNER]:
            return True

        if getattr(user, 'role', None) != UserRole.GYM_MANAGER:
            return False

        policy = cls._get_policy(user)
        if policy is None:
            return False

        return policy.has_permission(app, resource, action)

    @classmethod
    def has_any_app_permission(cls, user, app: str) -> bool:
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser or user.is_staff or getattr(user, 'role', None) in [UserRole.PLATFORM_ADMIN, UserRole.GYM_OWNER]:
            return True

        if getattr(user, 'role', None) != UserRole.GYM_MANAGER:
            return False

        policy = cls._get_policy(user)
        if policy is None:
            return False

        return policy.has_any_app_permission(app)

    @classmethod
    def get_or_create_policy(cls, manager: User, tenant) -> ManagerPermissionPolicy:
        policy = ManagerPermissionPolicy.all_objects.filter(manager=manager, tenant=tenant).first()
        if not policy:
            policy = ManagerPermissionPolicy.all_objects.create(
                manager=manager,
                tenant=tenant,
                permissions={},
                has_full_access=False
            )
        manager._cached_manager_policy = policy
        return policy

    @classmethod
    def update_policy(cls, manager: User, tenant, permissions_data: dict, has_full_access: bool = False) -> ManagerPermissionPolicy:
        sanitized = sanitize_permissions(permissions_data)
        policy = cls.get_or_create_policy(manager, tenant)
        policy.permissions = sanitized
        policy.has_full_access = bool(has_full_access)
        policy.save(update_fields=['permissions', 'has_full_access', 'updated_at'])
        manager._cached_manager_policy = policy
        return policy

    @classmethod
    def get_user_permissions(cls, user) -> dict:
        if not user or not user.is_authenticated:
            return {'has_full_access': False, 'permissions': {}}

        if user.is_superuser or user.is_staff or getattr(user, 'role', None) in [UserRole.PLATFORM_ADMIN, UserRole.GYM_OWNER]:
            full_matrix = {
                app: {res: list(res_data['actions']) for res, res_data in app_data['resources'].items()}
                for app, app_data in get_permission_catalog().items()
            }
            return {'has_full_access': True, 'permissions': full_matrix}

        if getattr(user, 'role', None) != UserRole.GYM_MANAGER:
            return {'has_full_access': False, 'permissions': {}}

        policy = cls._get_policy(user)
        if policy is None:
            return {'has_full_access': False, 'permissions': {}}

        return {
            'has_full_access': policy.has_full_access,
            'permissions': policy.permissions
        }

    @classmethod
    def get_manager_location_ids(cls, user):
        """
        Returns assigned location IDs for a Gym Manager.
        Returns None for users without manager location restrictions (Owner, Admin, superuser, or non-manager).
        Returns an empty list [] for a Manager with no assigned locations (blocking access to data).
        """
        if not user or not user.is_authenticated:
            return []

        if user.is_superuser or user.is_staff or getattr(user, 'role', None) in [UserRole.PLATFORM_ADMIN, UserRole.GYM_OWNER]:
            return None

        if getattr(user, 'role', None) != UserRole.GYM_MANAGER:
            return None

        loc_ids = set()
        if hasattr(user, 'staff_locations'):
            loc_ids.update(user.staff_locations.values_list('location_id', flat=True))
        if hasattr(user, 'profile') and user.profile and getattr(user.profile, 'primary_location_id', None):
            loc_ids.add(user.profile.primary_location_id)

        return list(loc_ids)

    @classmethod
    def is_manager_assigned_to_location(cls, user, location_id) -> bool:
        """
        Checks if a Gym Manager is assigned to a specific location.
        Always returns True for Owners, Platform Admins, superusers, and non-managers.
        """
        loc_ids = cls.get_manager_location_ids(user)
        if loc_ids is None:
            return True
        return str(location_id) in [str(lid) for lid in loc_ids]
