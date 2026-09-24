from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient
from apps.users.models import User, UserRole, ManagerPermissionPolicy
from apps.core.tenants.models import Tenant
from apps.users.permission_service import PermissionService
from apps.scheduling.models import Location


class ManagerPermissionTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Gym A", subdomain="gyma")
        self.tenant_b = Tenant.objects.create(name="Gym B", subdomain="gymb")

        self.owner_a = User.objects.create_user(
            email="owner_a@gyma.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant_a
        )
        self.manager_a = User.objects.create_user(
            email="manager_a@gyma.com",
            password="password123",
            role=UserRole.GYM_MANAGER,
            tenant=self.tenant_a
        )
        self.client_a = User.objects.create_user(
            email="client_a@gyma.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant_a
        )
        self.manager_b = User.objects.create_user(
            email="manager_b@gymb.com",
            password="password123",
            role=UserRole.GYM_MANAGER,
            tenant=self.tenant_b
        )

        self.location_a = Location.objects.create(
            tenant=self.tenant_a,
            name="Downtown",
            address="123 St",
            timezone="UTC"
        )
        self.client = APIClient(HTTP_X_TENANT_ID=str(self.tenant_a.id))

    def test_permissions_catalog_endpoint(self):
        self.client.force_authenticate(user=self.manager_a)
        response = self.client.get('/api/v1/users/permissions/catalog/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('scheduling', response.data)
        self.assertIn('workout', response.data)
        self.assertIn('payments', response.data)
        self.assertIn('classes', response.data['scheduling']['resources'])

    def test_new_manager_strict_zero_permissions(self):
        perms = PermissionService.get_user_permissions(self.manager_a)
        self.assertFalse(perms['has_full_access'])
        self.assertEqual(perms['permissions'], {})

        self.assertFalse(PermissionService.has_permission(self.manager_a, 'scheduling', 'classes', 'view'))
        self.assertFalse(PermissionService.has_permission(self.manager_a, 'scheduling', 'classes', 'create'))
        self.assertFalse(PermissionService.has_permission(self.manager_a, 'scheduling', 'events', 'create'))
        self.assertFalse(PermissionService.has_permission(self.manager_a, 'payments', 'transactions', 'view'))

    def test_owner_always_has_full_permissions(self):
        perms = PermissionService.get_user_permissions(self.owner_a)
        self.assertTrue(perms['has_full_access'])
        self.assertIn('scheduling', perms['permissions'])
        self.assertTrue(PermissionService.has_permission(self.owner_a, 'scheduling', 'classes', 'create'))
        self.assertTrue(PermissionService.has_permission(self.owner_a, 'payments', 'transactions', 'view'))

    def test_owner_configure_manager_permissions(self):
        self.client.force_authenticate(user=self.owner_a)
        payload = {
            'has_full_access': False,
            'permissions': {
                'scheduling': {
                    'classes': ['view', 'create'],
                    'events': ['view', 'create', 'roster']
                },
                'notifications': {
                    'campaigns': ['view']
                }
            }
        }
        url = f'/api/v1/users/{self.manager_a.id}/manager-permissions/'
        response = self.client.put(url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['has_full_access'])
        self.assertIn('scheduling', response.data['permissions'])

        self.manager_a.refresh_from_db()
        if hasattr(self.manager_a, '_cached_manager_policy'):
            delattr(self.manager_a, '_cached_manager_policy')

        self.assertTrue(PermissionService.has_permission(self.manager_a, 'scheduling', 'classes', 'view'))
        self.assertTrue(PermissionService.has_permission(self.manager_a, 'scheduling', 'classes', 'create'))
        self.assertFalse(PermissionService.has_permission(self.manager_a, 'scheduling', 'classes', 'delete'))
        self.assertTrue(PermissionService.has_permission(self.manager_a, 'scheduling', 'events', 'roster'))
        self.assertFalse(PermissionService.has_permission(self.manager_a, 'payments', 'transactions', 'view'))
        self.assertFalse(PermissionService.has_permission(self.manager_a, 'inventory', 'products', 'create'))

    def test_owner_grant_manager_full_access(self):
        self.client.force_authenticate(user=self.owner_a)
        payload = {'has_full_access': True, 'permissions': {}}
        url = f'/api/v1/users/{self.manager_a.id}/manager-permissions/'
        response = self.client.put(url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['has_full_access'])

        self.manager_a.refresh_from_db()
        if hasattr(self.manager_a, '_cached_manager_policy'):
            delattr(self.manager_a, '_cached_manager_policy')
        self.assertTrue(PermissionService.has_permission(self.manager_a, 'scheduling', 'classes', 'delete'))
        self.assertTrue(PermissionService.has_permission(self.manager_a, 'payments', 'gateways', 'edit'))

    def test_manager_my_permissions_endpoint(self):
        PermissionService.update_policy(
            manager=self.manager_a,
            tenant=self.tenant_a,
            permissions_data={'scheduling': {'events': ['view', 'create']}},
            has_full_access=False
        )
        self.client.force_authenticate(user=self.manager_a)
        response = self.client.get('/api/v1/users/managers/my-permissions/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['has_full_access'])
        self.assertIn('scheduling', response.data['permissions'])
        self.assertEqual(response.data['permissions']['scheduling']['events'], ['view', 'create'])

    def test_cross_tenant_owner_cannot_modify_other_manager(self):
        self.client.force_authenticate(user=self.owner_a)
        url = f'/api/v1/users/{self.manager_b.id}/manager-permissions/'
        response = self.client.put(url, data={'has_full_access': True, 'permissions': {}}, format='json')
        self.assertIn(response.status_code, [status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN])

    def test_manager_cannot_modify_permissions(self):
        self.client.force_authenticate(user=self.manager_a)
        url = f'/api/v1/users/{self.manager_a.id}/manager-permissions/'
        response = self.client.put(url, data={'has_full_access': True, 'permissions': {}}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_client_cannot_modify_permissions(self):
        self.client.force_authenticate(user=self.client_a)
        url = f'/api/v1/users/{self.manager_a.id}/manager-permissions/'
        response = self.client.put(url, data={'has_full_access': True, 'permissions': {}}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_api_scheduling_permission_enforcement(self):
        template_payload = {
            'location': str(self.location_a.id),
            'name': 'Morning Yoga',
            'duration_min': 45
        }

        self.client.force_authenticate(user=self.manager_a)
        post_response = self.client.post('/api/v1/scheduling/class-templates/', data=template_payload, format='json')
        self.assertEqual(post_response.status_code, status.HTTP_403_FORBIDDEN)

        PermissionService.update_policy(
            manager=self.manager_a,
            tenant=self.tenant_a,
            permissions_data={'scheduling': {'classes': ['view', 'create']}},
            has_full_access=False
        )

        post_response_granted = self.client.post('/api/v1/scheduling/class-templates/', data=template_payload, format='json')
        self.assertEqual(post_response_granted.status_code, status.HTTP_201_CREATED)
