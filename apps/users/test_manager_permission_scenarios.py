from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.core.tenants.models import Tenant
from apps.inventory.models import Product, StockTransaction
from apps.scheduling.models import (
    Booking, ClassSession, ClassTemplate, Event, EventEnrollment,
    Location, StaffAvailability, StaffLocation, SubstituteRequest, TenantBookingSettings,
    Waitlist
)
from apps.support.models import Ticket, TicketComment
from apps.users.models import User, UserRole
from apps.users.permission_service import PermissionService


class ManagerPermissionScenariosTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Gym", subdomain="testgym")
        self.owner = User.objects.create_user(
            email="owner@testgym.com",
            password="password123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant
        )
        self.manager = User.objects.create_user(
            email="manager@testgym.com",
            password="password123",
            role=UserRole.GYM_MANAGER,
            tenant=self.tenant
        )
        self.trainer = User.objects.create_user(
            email="trainer@testgym.com",
            password="password123",
            role=UserRole.TRAINER,
            tenant=self.tenant
        )
        self.client_user = User.objects.create_user(
            email="client@testgym.com",
            password="password123",
            role=UserRole.CLIENT,
            tenant=self.tenant
        )
        self.location = Location.objects.create(
            tenant=self.tenant,
            name="Main Studio",
            address="100 Main St",
            timezone="UTC"
        )
        StaffLocation.objects.create(
            staff=self.manager,
            location=self.location,
            tenant=self.tenant
        )
        StaffLocation.objects.create(
            staff=self.trainer,
            location=self.location,
            tenant=self.tenant
        )
        self.template = ClassTemplate.objects.create(
            tenant=self.tenant,
            location=self.location,
            name="Spin",
            duration_min=45
        )
        now = timezone.now()
        self.session = ClassSession.objects.create(
            tenant=self.tenant,
            template=self.template,
            staff=self.trainer,
            start_at=now + timedelta(days=1),
            end_at=now + timedelta(days=1, minutes=45),
            capacity=20
        )
        self.client = APIClient(HTTP_X_TENANT_ID=str(self.tenant.id))
        self.client.force_authenticate(user=self.manager)

    def set_manager_perms(self, perms_dict):
        policy = PermissionService.update_policy(
            manager=self.manager,
            tenant=self.tenant,
            permissions_data=perms_dict,
            has_full_access=False
        )
        self.manager._cached_manager_policy = policy

    def test_issue_1_locations_and_community(self):
        # 1. Without Locations permission, create is forbidden
        location_payload = {'name': 'Uptown Branch', 'address': '456 Uptown St', 'timezone': 'UTC'}
        res = self.client.post('/api/v1/scheduling/locations/', data=location_payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Permissions assigned for Templates (classes) do NOT affect Locations
        self.set_manager_perms({'scheduling': {'classes': ['view', 'create', 'edit']}})
        res_loc = self.client.post('/api/v1/scheduling/locations/', data=location_payload, format='json')
        self.assertEqual(res_loc.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Manager without Community permissions cannot view feed
        res_feed = self.client.get('/api/v1/socialnetwork/feed/')
        self.assertEqual(res_feed.status_code, status.HTTP_403_FORBIDDEN)

        # 4. When Locations and Community permissions are assigned, they work independently
        self.set_manager_perms({
            'scheduling': {'locations': ['view', 'create']},
            'socialnetwork': {'posts': ['view']}
        })
        res_create_loc = self.client.post('/api/v1/scheduling/locations/', data=location_payload, format='json')
        self.assertEqual(res_create_loc.status_code, status.HTTP_201_CREATED)
        created_loc_id = res_create_loc.data['id']

        # Created location is visible afterward
        res_list_loc = self.client.get('/api/v1/scheduling/locations/')
        self.assertEqual(res_list_loc.status_code, status.HTTP_200_OK)
        loc_ids = [l['id'] for l in (res_list_loc.data if isinstance(res_list_loc.data, list) else res_list_loc.data.get('results', []))]
        self.assertIn(created_loc_id, loc_ids)

        # Community feed is viewable
        res_feed_ok = self.client.get('/api/v1/socialnetwork/feed/')
        self.assertEqual(res_feed_ok.status_code, status.HTTP_200_OK)

    @patch('apps.retention.tasks.recalculate_single_client_metrics.delay')
    @patch('apps.scheduling.tasks.process_waitlist_promotion_job.delay')
    def test_issue_2_bookings_and_waitlist(self, mock_waitlist_job, mock_retention_job):
        # Manager without Bookings permissions cannot create bookings
        booking_payload = {'session': str(self.session.id), 'client': str(self.client_user.id)}
        res_create = self.client.post('/api/v1/scheduling/bookings/', data=booking_payload, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_403_FORBIDDEN)

        # Manager cannot create waitlist entries without Waitlist permissions
        waitlist_payload = {'session': str(self.session.id), 'client': str(self.client_user.id)}
        res_wl = self.client.post('/api/v1/scheduling/waitlist/', data=waitlist_payload, format='json')
        self.assertEqual(res_wl.status_code, status.HTTP_403_FORBIDDEN)

        # Create booking directly as owner/system to test delete permission check
        booking = Booking.objects.create(
            tenant=self.tenant,
            session=self.session,
            client=self.client_user,
            status='booked'
        )

        # Manager without delete permission cannot delete booking
        res_del = self.client.delete(f'/api/v1/scheduling/bookings/{booking.id}/')
        self.assertEqual(res_del.status_code, status.HTTP_403_FORBIDDEN)

        # Assign Bookings permissions only
        self.set_manager_perms({'scheduling': {'bookings': ['view', 'create', 'delete']}})
        res_del_ok = self.client.delete(f'/api/v1/scheduling/bookings/{booking.id}/')
        self.assertIn(res_del_ok.status_code, [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT])

        # Waitlist still forbidden because Waitlist permissions are independent
        res_wl_still_blocked = self.client.post('/api/v1/scheduling/waitlist/', data=waitlist_payload, format='json')
        self.assertEqual(res_wl_still_blocked.status_code, status.HTTP_403_FORBIDDEN)

    def test_issue_3_support(self):
        # Manager without Support access cannot view tickets
        res_view = self.client.get('/api/v1/support/tickets/')
        self.assertEqual(res_view.status_code, status.HTTP_403_FORBIDDEN)

        # Assign full Support permissions
        self.set_manager_perms({'support': {'tickets': ['view', 'create', 'edit', 'delete']}})

        # Can view tickets
        res_view_ok = self.client.get('/api/v1/support/tickets/')
        self.assertEqual(res_view_ok.status_code, status.HTTP_200_OK)

        # Can create ticket
        ticket_payload = {'title': 'AC broken', 'description': 'Main studio AC is leaking', 'priority': 'high'}
        res_create = self.client.post('/api/v1/support/tickets/', data=ticket_payload, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_201_CREATED)
        ticket_id = res_create.data['id']

        # Can add comment
        res_comment = self.client.post(f'/api/v1/support/tickets/{ticket_id}/add_comment/', data={'body': 'Technician notified'}, format='json')
        self.assertEqual(res_comment.status_code, status.HTTP_201_CREATED)

        # Can edit ticket
        res_edit = self.client.patch(f'/api/v1/support/tickets/{ticket_id}/', data={'status': 'in_progress'}, format='json')
        self.assertEqual(res_edit.status_code, status.HTTP_200_OK)

        # Can delete ticket
        res_del = self.client.delete(f'/api/v1/support/tickets/{ticket_id}/')
        self.assertEqual(res_del.status_code, status.HTTP_204_NO_CONTENT)

    def test_issue_4_waitlists(self):
        # Manager without Waitlist permissions cannot view, create, or delete
        res_view = self.client.get('/api/v1/scheduling/waitlist/')
        self.assertEqual(res_view.status_code, status.HTTP_403_FORBIDDEN)

        wl_payload = {'session': str(self.session.id), 'client': str(self.client_user.id)}
        res_create = self.client.post('/api/v1/scheduling/waitlist/', data=wl_payload, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_403_FORBIDDEN)

        # Assign Waitlist permissions
        self.set_manager_perms({'scheduling': {'waitlist': ['view', 'create', 'delete']}})

        res_create_ok = self.client.post('/api/v1/scheduling/waitlist/', data=wl_payload, format='json')
        self.assertEqual(res_create_ok.status_code, status.HTTP_201_CREATED)
        wl_id = res_create_ok.data['id']

        res_view_ok = self.client.get('/api/v1/scheduling/waitlist/')
        self.assertEqual(res_view_ok.status_code, status.HTTP_200_OK)

        res_del_ok = self.client.delete(f'/api/v1/scheduling/waitlist/{wl_id}/')
        self.assertEqual(res_del_ok.status_code, status.HTTP_204_NO_CONTENT)

    @patch('apps.scheduling.tasks.process_substitute_broadcast_job.delay')
    def test_issue_5_substitutes(self, mock_broadcast):
        # Manager without Substitutes permissions cannot create or delete
        sub_payload = {
            'session': str(self.session.id)
        }
        res_create = self.client.post('/api/v1/scheduling/substitute-requests/', data=sub_payload, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_403_FORBIDDEN)

        # Assign Substitutes permissions
        self.set_manager_perms({'scheduling': {'substitutes': ['view', 'create', 'edit', 'delete']}})

        res_create_ok = self.client.post('/api/v1/scheduling/substitute-requests/', data=sub_payload, format='json')
        self.assertEqual(res_create_ok.status_code, status.HTTP_201_CREATED)
        sub_id = res_create_ok.data['id']

        # Accept / offer substitute with trainer specified
        res_accept = self.client.post(
            f'/api/v1/scheduling/substitute-requests/{sub_id}/accept/',
            data={'trainer': str(self.trainer.id)},
            format='json'
        )
        self.assertEqual(res_accept.status_code, status.HTTP_200_OK)

        # Delete substitute request
        res_del = self.client.delete(f'/api/v1/scheduling/substitute-requests/{sub_id}/')
        self.assertEqual(res_del.status_code, status.HTTP_204_NO_CONTENT)

    def test_issue_6_scheduling_and_sessions(self):
        # Manager without view or create permissions cannot view or create sessions
        res_view = self.client.get('/api/v1/scheduling/sessions/')
        self.assertEqual(res_view.status_code, status.HTTP_403_FORBIDDEN)

        now = timezone.now()
        session_payload = {
            'template': str(self.template.id),
            'staff': str(self.trainer.id),
            'start_at': (now + timedelta(days=2)).isoformat(),
            'end_at': (now + timedelta(days=2, minutes=45)).isoformat(),
            'capacity': 15
        }
        res_create = self.client.post('/api/v1/scheduling/sessions/', data=session_payload, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_403_FORBIDDEN)

        # Manager with ONLY edit permission can edit normal fields, but CANNOT cancel session
        self.set_manager_perms({'scheduling': {'sessions': ['view', 'edit']}})

        # Normal edit works
        res_edit = self.client.patch(f'/api/v1/scheduling/sessions/{self.session.id}/', data={'capacity': 25}, format='json')
        self.assertEqual(res_edit.status_code, status.HTTP_200_OK)

        # Session cancellation via PATCH is blocked without cancel_session permission
        res_cancel_patch_flag = self.client.patch(
            f'/api/v1/scheduling/sessions/{self.session.id}/',
            data={'is_cancel': True},
            format='json'
        )
        self.assertEqual(res_cancel_patch_flag.status_code, status.HTTP_403_FORBIDDEN)

        res_cancel_patch_status = self.client.patch(
            f'/api/v1/scheduling/sessions/{self.session.id}/',
            data={'status': 'cancelled'},
            format='json'
        )
        self.assertEqual(res_cancel_patch_status.status_code, status.HTTP_403_FORBIDDEN)

        # Now grant cancel_session permission
        self.set_manager_perms({'scheduling': {'sessions': ['view', 'edit', 'cancel_session']}})
        res_cancel_ok = self.client.patch(
            f'/api/v1/scheduling/sessions/{self.session.id}/',
            data={'is_cancel': True, 'status': 'cancelled'},
            format='json'
        )
        self.assertEqual(res_cancel_ok.status_code, status.HTTP_200_OK)

    def test_issue_7_workshops_and_events(self):
        now = timezone.now()
        event = Event.objects.create(
            tenant=self.tenant,
            location=self.location,
            title="Kettlebell Masterclass",
            start_at=now + timedelta(days=5),
            end_at=now + timedelta(days=5, hours=2),
            capacity=10,
            is_free=True
        )

        # Manager has View, Create, Edit permissions for Events, but NOT Roster or Check-in
        self.set_manager_perms({'scheduling': {'events': ['view', 'create', 'edit']}})

        # Roster view/action blocked
        res_roster = self.client.get(f'/api/v1/scheduling/workshops/{event.id}/roster/')
        self.assertEqual(res_roster.status_code, status.HTTP_403_FORBIDDEN)

        # Enrolling client onto roster blocked
        res_enroll = self.client.post(f'/api/v1/scheduling/workshops/{event.id}/enroll/', data={
            'client_id': str(self.client_user.id),
            'is_complimentary': True
        }, format='json')
        self.assertEqual(res_enroll.status_code, status.HTTP_403_FORBIDDEN)

        # Check-in blocked
        res_checkin = self.client.post(f'/api/v1/scheduling/workshops/{event.id}/check_in/', data={
            'client_id': str(self.client_user.id)
        }, format='json')
        self.assertEqual(res_checkin.status_code, status.HTTP_403_FORBIDDEN)

        # Grant Roster and Check-in permissions
        self.set_manager_perms({'scheduling': {'events': ['view', 'create', 'edit', 'roster', 'check_in']}})

        res_enroll_ok = self.client.post(f'/api/v1/scheduling/workshops/{event.id}/enroll/', data={
            'client_id': str(self.client_user.id),
            'is_complimentary': True
        }, format='json')
        self.assertEqual(res_enroll_ok.status_code, status.HTTP_201_CREATED)

        res_roster_ok = self.client.get(f'/api/v1/scheduling/workshops/{event.id}/roster/')
        self.assertEqual(res_roster_ok.status_code, status.HTTP_200_OK)

        res_checkin_ok = self.client.post(f'/api/v1/scheduling/workshops/{event.id}/check_in/', data={
            'client_id': str(self.client_user.id)
        }, format='json')
        self.assertEqual(res_checkin_ok.status_code, status.HTTP_200_OK)

    def test_issue_8_staff_availability(self):
        # Manager without Staff Availability permissions cannot add hours or blackout
        avail_payload = {
            'staff': str(self.trainer.id),
            'weekday_or_date': 'monday',
            'start_time': '09:00:00',
            'end_time': '17:00:00',
            'is_blackout': False
        }
        res_create = self.client.post('/api/v1/scheduling/staff-availability/', data=avail_payload, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_403_FORBIDDEN)

        blackout_payload = {
            'staff': str(self.trainer.id),
            'weekday_or_date': '2026-12-25',
            'start_time': '00:00:00',
            'end_time': '23:59:59',
            'is_blackout': True
        }
        res_blackout = self.client.post('/api/v1/scheduling/staff-availability/', data=blackout_payload, format='json')
        self.assertEqual(res_blackout.status_code, status.HTTP_403_FORBIDDEN)

        # Grant Staff Availability permissions
        self.set_manager_perms({'scheduling': {'staff_availability': ['view', 'create', 'delete']}})

        res_create_ok = self.client.post('/api/v1/scheduling/staff-availability/', data=avail_payload, format='json')
        self.assertEqual(res_create_ok.status_code, status.HTTP_201_CREATED)
        avail_id = res_create_ok.data['id']

        res_del_ok = self.client.delete(f'/api/v1/scheduling/staff-availability/{avail_id}/')
        self.assertEqual(res_del_ok.status_code, status.HTTP_204_NO_CONTENT)

    def test_issue_9_booking_settings(self):
        # Manager with Classes Edit permission alone CANNOT access booking settings
        self.set_manager_perms({'scheduling': {'classes': ['edit']}})

        res_get = self.client.get('/api/v1/scheduling/booking-settings/')
        self.assertEqual(res_get.status_code, status.HTTP_403_FORBIDDEN)

        res_patch = self.client.patch('/api/v1/scheduling/booking-settings/', data={'cancellation_window_hours': 6}, format='json')
        self.assertEqual(res_patch.status_code, status.HTTP_403_FORBIDDEN)

        # Assign Booking Settings permissions
        self.set_manager_perms({'scheduling': {'booking_settings': ['view', 'edit']}})

        res_get_ok = self.client.get('/api/v1/scheduling/booking-settings/')
        self.assertEqual(res_get_ok.status_code, status.HTTP_200_OK)

        res_patch_ok = self.client.patch('/api/v1/scheduling/booking-settings/', data={'cancellation_window_hours': 6}, format='json')
        self.assertEqual(res_patch_ok.status_code, status.HTTP_200_OK)

    @patch('stripe.Account.create')
    @patch('stripe.AccountLink.create')
    def test_issue_10_payments_catalog(self, mock_link, mock_account):
        mock_account.return_value = type('MockAccount', (), {'id': 'acct_123'})()
        mock_link.return_value = type('MockLink', (), {'url': 'https://stripe.com/onboard'})()

        # Manager without payments permissions cannot access finance summary
        res_summary = self.client.get('/api/v1/payments/tenant/finance/summary/')
        self.assertEqual(res_summary.status_code, status.HTTP_403_FORBIDDEN)

        # Assign Payments permissions (transactions, subscriptions, gateways)
        self.set_manager_perms({
            'payments': {
                'transactions': ['view'],
                'subscriptions': ['view'],
                'gateways': ['edit']
            }
        })

        # Finance summary works and does NOT say "Gym Owner can only view financial summaries"
        res_summary_ok = self.client.get('/api/v1/payments/tenant/finance/summary/')
        self.assertEqual(res_summary_ok.status_code, status.HTTP_200_OK)
        self.assertNotIn("Gym Owner can only view financial summaries", str(res_summary_ok.data))

        # Ledger list works
        res_ledger = self.client.get('/api/v1/payments/tenant-ledgers/')
        self.assertEqual(res_ledger.status_code, status.HTTP_200_OK)

        # Billing subscription works
        res_sub = self.client.get('/api/v1/payments/billing/subscription/')
        self.assertEqual(res_sub.status_code, status.HTTP_200_OK)

        # Payment gateway connect works
        res_gw = self.client.post('/api/v1/payments/connect/')
        self.assertEqual(res_gw.status_code, status.HTTP_200_OK)

    def test_issue_11_inventory_granular_permissions_and_visibility(self):
        # Manager has only View Products and View Stock Moves
        self.set_manager_perms({
            'inventory': {
                'products': ['view'],
                'stock': ['view'],
                'categories': ['view']
            }
        })

        # Can view products list
        res_list = self.client.get('/api/v1/inventory/products/')
        self.assertEqual(res_list.status_code, status.HTTP_200_OK)

        # Cannot create product without Create permission
        prod_payload = {
            'name': 'Whey Protein 1kg',
            'sku': 'WP-VAN-001',
            'price': '49.99',
            'location': str(self.location.id)
        }
        res_create = self.client.post('/api/v1/inventory/products/', data=prod_payload, format='json')
        self.assertEqual(res_create.status_code, status.HTTP_403_FORBIDDEN)

        # Cannot create stock transactions without adjust_stock permission
        stock_payload = {
            'quantity': 20,
            'transaction_type': 'restock',
            'notes': 'Initial delivery'
        }
        res_stock = self.client.post('/api/v1/inventory/stock-transactions/', data=stock_payload, format='json')
        self.assertEqual(res_stock.status_code, status.HTTP_403_FORBIDDEN)

        # Now grant Products Create permission
        self.set_manager_perms({
            'inventory': {
                'products': ['view', 'create'],
                'stock': ['view', 'adjust_stock']
            }
        })

        # Creation succeeds
        res_create_ok = self.client.post('/api/v1/inventory/products/', data=prod_payload, format='json')
        self.assertEqual(res_create_ok.status_code, status.HTTP_201_CREATED)
        created_prod_id = res_create_ok.data['id']

        # Newly created product is visible to the manager
        res_list_after = self.client.get('/api/v1/inventory/products/')
        self.assertEqual(res_list_after.status_code, status.HTTP_200_OK)
        prod_ids = [p['id'] for p in (res_list_after.data if isinstance(res_list_after.data, list) else res_list_after.data.get('results', []))]
        self.assertIn(created_prod_id, prod_ids)

        # Stock adjustment succeeds
        stock_payload['product'] = created_prod_id
        res_stock_ok = self.client.post('/api/v1/inventory/stock-transactions/', data=stock_payload, format='json')
        self.assertEqual(res_stock_ok.status_code, status.HTTP_201_CREATED)
