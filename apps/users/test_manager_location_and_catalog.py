from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status
from rest_framework.exceptions import ValidationError

from apps.core.tenants.models import Tenant
from apps.users.models import User, UserRole, UserProfile, ManagerPermissionPolicy
from apps.users.permission_service import PermissionService
from apps.core.permissions_catalog import PERMISSION_CATALOG, sanitize_permissions
from apps.users.services import UserService
from apps.scheduling.models import Location, Room, ClassTemplate, StaffLocation
from apps.inventory.models import Product


class ManagerLocationAndCatalogTestCase(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Studio", subdomain="teststudio")
        from apps.core.tenants.context import set_current_tenant
        set_current_tenant(self.tenant)
        self.client = APIClient(HTTP_X_TENANT_ID=str(self.tenant.id))

        # Create locations
        self.loc_a = Location.objects.create(tenant=self.tenant, name="Downtown Branch")
        self.loc_b = Location.objects.create(tenant=self.tenant, name="Uptown Branch")

        # Create Owner
        self.owner = User.objects.create_user(
            email="owner@test.com",
            password="Password123!",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant,
            first_name="Studio",
            last_name="Owner"
        )
        UserProfile.objects.create(user=self.owner, primary_location=self.loc_a)

    def test_permission_catalog_contains_all_new_and_owner_endpoints(self):
        """Ensure all required endpoints exist in the permission catalog and can be assigned."""
        expected_modules = {
            'scheduling': ['locations', 'classes', 'sessions', 'events', 'rooms', 'spot_types', 'packages', 'bookings', 'appointments', 'waitlist', 'substitutes', 'staff_availability', 'facility_access', 'staff_assignments', 'reports', 'booking_settings'],
            'staff_users': ['clients', 'staff', 'staff_requests'],
            'workout': ['workouts', 'exercises', 'workout_logs', 'playlists', 'assignments'],
            'payments': ['payouts', 'billing'],
            'rewards': ['programs', 'badges', 'tiers', 'analytics', 'referrals'],
            'notifications': ['campaigns', 'templates', 'automations', 'groups', 'settings'],
            'nutrition': ['food_items', 'meals', 'recipes', 'goals', 'food_logs'],
            'socialnetwork': ['posts', 'groups', 'moderation'],
        }

        for app, resources in expected_modules.items():
            self.assertIn(app, PERMISSION_CATALOG, f"App {app} missing from PERMISSION_CATALOG")
            for res in resources:
                self.assertIn(res, PERMISSION_CATALOG[app]['resources'], f"Resource {res} missing from {app} in catalog")

    def test_admin_can_assign_new_endpoints_to_manager(self):
        """Admin can assign permissions for newly cataloged endpoints to a manager."""
        custom_perms = {
            'scheduling': {
                'appointments': ['view', 'create', 'edit', 'delete'],
                'facility_access': ['view', 'log_access'],
                'reports': ['view'],
            },
            'payments': {
                'billing': ['view', 'edit'],
            },
            'rewards': {
                'programs': ['view', 'create'],
                'badges': ['view', 'create'],
            },
            'notifications': {
                'groups': ['view', 'create'],
                'settings': ['view', 'edit'],
            }
        }
        sanitized = sanitize_permissions(custom_perms)
        self.assertEqual(sanitized['scheduling']['appointments'], ['view', 'create', 'edit', 'delete'])
        self.assertEqual(sanitized['payments']['billing'], ['view', 'edit'])
        self.assertEqual(sanitized['rewards']['badges'], ['view', 'create'])
        self.assertEqual(sanitized['notifications']['settings'], ['view', 'edit'])

    def test_create_manager_without_location_fails(self):
        """Creating a GYM_MANAGER without any location assignment must raise ValidationError."""
        from django.core.exceptions import ValidationError as DjangoValidationError
        with self.assertRaises((ValidationError, DjangoValidationError)) as ctx:
            UserService.create_user_with_profile(
                email="manager_noloc@test.com",
                password="Password123!",
                role=UserRole.GYM_MANAGER,
                tenant=self.tenant,
                location_ids=[]
            )
        self.assertIn("location", str(ctx.exception).lower())

    def test_create_manager_with_single_and_multiple_locations_succeeds(self):
        """Manager can be assigned to one or multiple locations."""
        # Single location
        mgr1 = UserService.create_user_with_profile(
            email="mgr1@test.com",
            password="Password123!",
            role=UserRole.GYM_MANAGER,
            tenant=self.tenant,
            location_ids=[self.loc_a.id]
        )
        self.assertEqual(mgr1.role, UserRole.GYM_MANAGER)
        self.assertEqual(mgr1.profile.primary_location, self.loc_a)
        self.assertTrue(StaffLocation.all_objects.filter(staff=mgr1, location=self.loc_a).exists())
        self.assertEqual(PermissionService.get_manager_location_ids(mgr1), [self.loc_a.id])

        # Multiple locations
        mgr2 = UserService.create_user_with_profile(
            email="mgr2@test.com",
            password="Password123!",
            role=UserRole.GYM_MANAGER,
            tenant=self.tenant,
            location_ids=[self.loc_a.id, self.loc_b.id]
        )
        self.assertIn(mgr2.profile.primary_location, [self.loc_a, self.loc_b])
        self.assertEqual(StaffLocation.all_objects.filter(staff=mgr2).count(), 2)
        loc_ids = PermissionService.get_manager_location_ids(mgr2)
        self.assertIn(self.loc_a.id, loc_ids)
        self.assertIn(self.loc_b.id, loc_ids)

    def test_cannot_delete_last_location_of_manager(self):
        """Deleting the last assigned location of a GYM_MANAGER must be blocked."""
        mgr = UserService.create_user_with_profile(
            email="mgr_lastloc@test.com",
            password="Password123!",
            role=UserRole.GYM_MANAGER,
            tenant=self.tenant,
            location_ids=[self.loc_a.id]
        )
        staff_loc = StaffLocation.all_objects.get(staff=mgr, location=self.loc_a)

        self.client.force_authenticate(user=self.owner)
        url = f"/api/v1/scheduling/staff-locations/{staff_loc.id}/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("at least one assigned location", response.data.get('detail', ''))
        self.assertTrue(StaffLocation.all_objects.filter(id=staff_loc.id).exists())

    def test_manager_data_scoping_scheduling_and_inventory(self):
        """Manager only sees resources associated with their assigned locations."""
        mgr = UserService.create_user_with_profile(
            email="mgr_scoped@test.com",
            password="Password123!",
            role=UserRole.GYM_MANAGER,
            tenant=self.tenant,
            location_ids=[self.loc_a.id]
        )
        # Give permissions to the manager
        PermissionService.update_policy(
            manager=mgr,
            tenant=self.tenant,
            permissions_data={
                'scheduling': {'classes': ['view', 'create']},
                'inventory': {'products': ['view', 'create']}
            },
            has_full_access=False
        )

        # Create resources in Location A and Location B
        tmpl_a = ClassTemplate.objects.create(
            tenant=self.tenant, location=self.loc_a, name="Yoga A", duration_min=60, default_capacity=10
        )
        tmpl_b = ClassTemplate.objects.create(
            tenant=self.tenant, location=self.loc_b, name="Spin B", duration_min=45, default_capacity=15
        )

        prod_a = Product.objects.create(
            tenant=self.tenant, location=self.loc_a, name="Water Bottle A", sku="WTR-A", price=2.50
        )
        prod_b = Product.objects.create(
            tenant=self.tenant, location=self.loc_b, name="Energy Bar B", sku="BAR-B", price=3.00
        )

        self.client.force_authenticate(user=mgr)

        # Class templates list (only sees Location A)
        resp = self.client.get("/api/v1/scheduling/class-templates/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        results = resp.data if isinstance(resp.data, list) else resp.data.get('results', [])
        ids = [item['id'] for item in results]
        self.assertIn(str(tmpl_a.id), ids)
        self.assertNotIn(str(tmpl_b.id), ids)

        # Products list (only sees Location A)
        resp_prod = self.client.get("/api/v1/inventory/products/")
        self.assertEqual(resp_prod.status_code, status.HTTP_200_OK)
        results_prod = resp_prod.data if isinstance(resp_prod.data, list) else resp_prod.data.get('results', [])
        prod_ids = [item['id'] for item in results_prod]
        self.assertIn(str(prod_a.id), prod_ids)
        self.assertNotIn(str(prod_b.id), prod_ids)

        # Manager CAN create product in assigned location A
        resp_create_a = self.client.post("/api/v1/inventory/products/", {
            "name": "Valid Product",
            "sku": "VAL-1",
            "price": "10.00",
            "location": str(self.loc_a.id)
        })
        self.assertEqual(resp_create_a.status_code, status.HTTP_201_CREATED)

        # Manager CANNOT create product in unassigned location B
        resp_create = self.client.post("/api/v1/inventory/products/", {
            "name": "Invalid Product",
            "sku": "INV-1",
            "price": "10.00",
            "location": str(self.loc_b.id)
        })
        self.assertEqual(resp_create.status_code, status.HTTP_403_FORBIDDEN)
