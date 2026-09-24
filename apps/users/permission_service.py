from apps.users.models import User, UserRole, ManagerPermissionPolicy
from apps.core.permissions_catalog import sanitize_permissions, get_permission_catalog


class PermissionService:
    @classmethod
    def _get_policy(cls, user):
        if hasattr(user, '_cached_manager_policy'):
            return user._cached_manager_policy
        policy = None
        try:
            policy = user.permission_policy
        except Exception:
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
