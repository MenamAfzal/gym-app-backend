from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
import json
import time

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from apps.core.tenants.models import Tenant
from apps.ghl.client import GHLClient, GHLApiError, GHLAuthError, GHLRateLimitError
from apps.ghl.models import GHLOutboxEvent, TenantGHLConnection
from apps.ghl.oauth import (
    generate_oauth_state,
    validate_oauth_state,
    get_authorization_url,
    refresh_connection_tokens,
)
from apps.ghl.services import GHLIntegrationService
from apps.ghl.tasks import deliver_ghl_outbox_event_task
from apps.payments.models import ExternalCheckoutSession
from apps.scheduling.models import Location, Package, PackageType, Payment
from apps.users.models import User, UserProfile, UserRole
from apps.users.services import UserService


class GHLOAuthAndConnectionTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = Tenant.objects.create(
            name="Core & Cain Fitness",
            subdomain="core-cain",
            currency="usd",
            is_active=True,
        )
        self.admin_user = UserService.create_user_with_profile(
            email="owner@corecain.com",
            password="securepassword123",
            role=UserRole.GYM_OWNER,
            tenant=self.tenant,
            first_name="Core",
            last_name="Owner",
            phone_number="+15550001111",
        )

    def test_connect_endpoint_generates_valid_auth_url(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/v1/ghl/oauth/connect/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("auth_url", response.data)
        auth_url = response.data["auth_url"]
        self.assertIn("response_type=code", auth_url)
        self.assertIn("state=", auth_url)

    def test_state_validation_binds_to_tenant(self):
        state = generate_oauth_state(self.tenant)
        resolved_tenant = validate_oauth_state(state)
        self.assertEqual(resolved_tenant.id, self.tenant.id)

    def test_state_validation_rejects_tampered_or_expired_state(self):
        with self.assertRaises(Exception):
            validate_oauth_state("tampered_state_token")

        expired_state = generate_oauth_state(self.tenant)
        with self.assertRaises(Exception):
            validate_oauth_state(expired_state, max_age_seconds=-1)

    @patch("apps.ghl.oauth.requests.post")
    def test_callback_endpoint_exchanges_code_and_creates_connection(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "pit-test-access-token-123",
            "refresh_token": "pit-test-refresh-token-456",
            "expires_in": 86400,
            "locationId": "loc_core_and_cain_001",
            "companyId": "comp_123",
            "userId": "user_123",
            "userType": "Location",
            "scope": "contacts.readonly contacts.write locations.readonly",
        }
        mock_post.return_value = mock_response

        state = generate_oauth_state(self.tenant)
        response = self.client.get(f'/api/v1/ghl/oauth/callback/?code=valid_code_xyz&state={state}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "connected")
        self.assertEqual(response.data["location_id"], "loc_core_and_cain_001")

        # Verify database record
        connection = TenantGHLConnection.all_objects.get(tenant=self.tenant)
        self.assertEqual(connection.status, TenantGHLConnection.Status.CONNECTED)
        self.assertEqual(connection.location_id, "loc_core_and_cain_001")
        self.assertEqual(connection.access_token, "pit-test-access-token-123")
        self.assertEqual(connection.refresh_token, "pit-test-refresh-token-456")
        self.assertTrue(connection.is_connected)

    def test_status_and_disconnect_endpoints(self):
        connection = TenantGHLConnection.all_objects.create(
            tenant=self.tenant,
            location_id="loc_core_and_cain_001",
            status=TenantGHLConnection.Status.CONNECTED,
            connected_at=timezone.now(),
        )
        connection.access_token = "valid_tok"
        connection.save()

        self.client.force_authenticate(user=self.admin_user)
        # 1. Status
        res_status = self.client.get('/api/v1/ghl/status/')
        self.assertEqual(res_status.status_code, status.HTTP_200_OK)
        self.assertTrue(res_status.data["connected"])
        self.assertEqual(res_status.data["location_id"], "loc_core_and_cain_001")

        # 2. Disconnect
        res_disc = self.client.post('/api/v1/ghl/disconnect/')
        self.assertEqual(res_disc.status_code, status.HTTP_200_OK)
        self.assertEqual(res_disc.data["status"], "disconnected")

        connection.refresh_from_db()
        self.assertEqual(connection.status, TenantGHLConnection.Status.DISCONNECTED)
        self.assertFalse(connection.is_connected)
        self.assertEqual(connection.access_token, "")


class GHLTokenRefreshTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Core & Cain", subdomain="core-cain-refresh")
        self.connection = TenantGHLConnection.all_objects.create(
            tenant=self.tenant,
            location_id="loc_123",
            status=TenantGHLConnection.Status.CONNECTED,
            token_expires_at=timezone.now() - timedelta(minutes=10),
        )
        self.connection.access_token = "old_access_token"
        self.connection.refresh_token = "valid_refresh_token"
        self.connection.save()

    @patch("apps.ghl.oauth.requests.post")
    def test_refresh_connection_tokens(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_refreshed_access_token",
            "refresh_token": "new_refreshed_refresh_token",
            "expires_in": 7200,
        }
        mock_post.return_value = mock_response

        updated = refresh_connection_tokens(self.connection)
        self.assertEqual(updated.access_token, "new_refreshed_access_token")
        self.assertEqual(updated.refresh_token, "new_refreshed_refresh_token")
        self.assertFalse(updated.is_token_expired)


class GHLPurchaseSyncTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Core & Cain", subdomain="core-cain-sync", currency="usd")
        self.connection = TenantGHLConnection.all_objects.create(
            tenant=self.tenant,
            location_id="loc_cc_sync",
            status=TenantGHLConnection.Status.CONNECTED,
            token_expires_at=timezone.now() + timedelta(days=1),
        )
        self.connection.access_token = "tok_test_sync"
        self.connection.refresh_token = "ref_test_sync"
        self.connection.save()

        self.client_user = UserService.create_user_with_profile(
            email="athlete@example.com",
            password="athletepassword123",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            first_name="Jane",
            last_name="Doe",
            phone_number="+15551234567",
        )

        self.gym_location = Location.objects.create(
            tenant=self.tenant,
            name="Main Gym Facility",
        )

        self.package_type = PackageType.objects.create(
            tenant=self.tenant,
            location=self.gym_location,
            name="10-Class Elite Pack",
            price=Decimal("150.00"),
            credit_count=10,
            validity_days=30,
        )
        self.package = Package.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            package_type=self.package_type,
            credits_remaining=10,
            status="active",
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.payment = Payment.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            package=self.package,
            amount=Decimal("150.00"),
            status="completed",
            type="package_purchase",
            provider_ref="pi_test_12345",
        )

    @patch("apps.ghl.client.GHLClient._request")
    def test_sync_creates_new_contact_when_not_found(self, mock_request):
        def mock_request_side_effect(method, endpoint, params=None, json_data=None):
            if method == "GET" and "/contacts/" in endpoint:
                return {"contacts": []}
            if method == "POST" and endpoint == "/contacts/":
                return {"contact": {"id": "ghl_new_contact_777"}}
            if method == "POST" and "/notes" in endpoint:
                return {"note": {"id": "note_123"}}
            return {}

        mock_request.side_effect = mock_request_side_effect

        event = GHLIntegrationService.dispatch_purchase_event(
            package=self.package,
            payment=self.payment,
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.status, GHLOutboxEvent.Status.PENDING)
        self.assertEqual(event.payload["customer_email"], "athlete@example.com")
        self.assertEqual(event.payload["customer_phone"], "+15551234567")
        self.assertEqual(event.payload["package_name"], "10-Class Elite Pack")
        self.assertEqual(event.payload["amount_paid"], 150.0)

        # Trigger delivery task
        deliver_ghl_outbox_event_task(str(event.id))
        event.refresh_from_db()
        self.assertEqual(event.status, GHLOutboxEvent.Status.DELIVERED)
        self.assertEqual(event.response_status_code, 200)

        # Verify POST to /contacts/ payload
        post_calls = [call for call in mock_request.call_args_list if call[0][0] == "POST" and call[0][1] == "/contacts/"]
        self.assertEqual(len(post_calls), 1)
        created_payload = post_calls[0][1]["json_data"]
        self.assertEqual(created_payload["email"], "athlete@example.com")
        self.assertEqual(created_payload["phone"], "+15551234567")
        self.assertEqual(created_payload["locationId"], "loc_cc_sync")
        self.assertIn("Package: 10-Class Elite Pack", created_payload["tags"])
        self.assertIn("Paid: USD 150.00", created_payload["tags"])

    @patch("apps.ghl.client.GHLClient._request")
    def test_sync_updates_existing_contact_when_found(self, mock_request):
        def mock_request_side_effect(method, endpoint, params=None, json_data=None):
            if method == "GET" and "/contacts/" in endpoint:
                return {
                    "contacts": [
                        {
                            "id": "ghl_existing_contact_888",
                            "email": "athlete@example.com",
                            "phone": "+15551234567",
                            "tags": ["member", "lead"],
                        }
                    ]
                }
            if method == "PUT" and "/contacts/ghl_existing_contact_888" in endpoint:
                return {"contact": {"id": "ghl_existing_contact_888"}}
            if method == "POST" and "/notes" in endpoint:
                return {"note": {"id": "note_456"}}
            return {}

        mock_request.side_effect = mock_request_side_effect

        event = GHLIntegrationService.dispatch_purchase_event(
            package=self.package,
            payment=self.payment,
        )
        deliver_ghl_outbox_event_task(str(event.id))
        event.refresh_from_db()
        self.assertEqual(event.status, GHLOutboxEvent.Status.DELIVERED)

        # Verify PUT was called on existing contact ID
        put_calls = [call for call in mock_request.call_args_list if call[0][0] == "PUT"]
        self.assertEqual(len(put_calls), 1)
        self.assertEqual(put_calls[0][0][1], "/contacts/ghl_existing_contact_888")
        updated_payload = put_calls[0][1]["json_data"]
        self.assertIn("Package: 10-Class Elite Pack", updated_payload["tags"])
        self.assertIn("Paid: USD 150.00", updated_payload["tags"])
        self.assertIn("member", updated_payload["tags"])

    def test_duplicate_purchase_prevention_idempotency(self):
        event1 = GHLIntegrationService.dispatch_purchase_event(
            package=self.package,
            payment=self.payment,
        )
        self.assertIsNotNone(event1)

        event2 = GHLIntegrationService.dispatch_purchase_event(
            package=self.package,
            payment=self.payment,
        )
        self.assertEqual(event1.id, event2.id)
        self.assertEqual(GHLOutboxEvent.all_objects.filter(tenant=self.tenant).count(), 1)

    def test_failed_and_pending_payments_do_not_sync(self):
        pending_payment = Payment.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            amount=Decimal("150.00"),
            status="pending",
            provider_ref="pi_pending_test_1",
            idempotency_key="pi_pending_test_1",
        )
        event_pending = GHLIntegrationService.dispatch_purchase_event(payment=pending_payment)
        self.assertIsNone(event_pending)

        failed_payment = Payment.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            amount=Decimal("150.00"),
            status="failed",
            provider_ref="pi_failed_test_2",
            idempotency_key="pi_failed_test_2",
        )
        event_failed = GHLIntegrationService.dispatch_purchase_event(payment=failed_payment)
        self.assertIsNone(event_failed)


class GHLTenantIsolationTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Core & Cain", subdomain="core-cain-iso")
        self.tenant_b = Tenant.objects.create(name="Metropolis Gym", subdomain="metro-gym-iso")

        self.conn_a = TenantGHLConnection.all_objects.create(
            tenant=self.tenant_a,
            location_id="loc_core_and_cain",
            status=TenantGHLConnection.Status.CONNECTED,
            token_expires_at=timezone.now() + timedelta(days=1),
        )
        self.conn_a.access_token = "token_tenant_a"
        self.conn_a.save()

        self.conn_b = TenantGHLConnection.all_objects.create(
            tenant=self.tenant_b,
            location_id="loc_metro_gym",
            status=TenantGHLConnection.Status.CONNECTED,
            token_expires_at=timezone.now() + timedelta(days=1),
        )
        self.conn_b.access_token = "token_tenant_b"
        self.conn_b.save()

        self.client_a = UserService.create_user_with_profile(
            email="client_a@corecain.com",
            password="pass",
            role=UserRole.CLIENT,
            tenant=self.tenant_a,
            first_name="Alice",
            last_name="A",
            phone_number="+10000000001",
        )

        self.payment_a = Payment.objects.create(
            tenant=self.tenant_a,
            client=self.client_a,
            amount=Decimal("200.00"),
            status="completed",
        )

    @patch("apps.ghl.client.GHLClient._request")
    def test_tenant_a_purchase_strictly_targets_tenant_a_location(self, mock_request):
        mock_request.return_value = {"contacts": [], "contact": {"id": "new_c_1"}}

        event_a = GHLIntegrationService.dispatch_purchase_event(payment=self.payment_a)
        self.assertEqual(event_a.tenant, self.tenant_a)

        deliver_ghl_outbox_event_task(str(event_a.id))

        # Check all mock requests made: verify locationId strictly matched Tenant A
        for call in mock_request.call_args_list:
            json_data = call[1].get("json_data") or {}
            params = call[1].get("params") or {}

            if "locationId" in json_data:
                self.assertNotEqual(json_data.get("locationId"), "loc_metro_gym")
                self.assertEqual(json_data.get("locationId"), "loc_core_and_cain")
            if "locationId" in params:
                self.assertNotEqual(params.get("locationId"), "loc_metro_gym")
                self.assertEqual(params.get("locationId"), "loc_core_and_cain")

        # Verify no events created under Tenant B
        self.assertEqual(GHLOutboxEvent.all_objects.filter(tenant=self.tenant_b).count(), 0)


class GHLRetriesAndFailuresTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Retry Gym", subdomain="retry-gym")
        self.conn = TenantGHLConnection.all_objects.create(
            tenant=self.tenant,
            location_id="loc_retry",
            status=TenantGHLConnection.Status.CONNECTED,
            token_expires_at=timezone.now() + timedelta(days=1),
        )
        self.conn.access_token = "tok_retry"
        self.conn.refresh_token = "ref_retry"
        self.conn.save()

        self.client_user = UserService.create_user_with_profile(
            email="retry_user@example.com",
            password="password",
            role=UserRole.CLIENT,
            tenant=self.tenant,
            first_name="Retry",
            last_name="User",
            phone_number="+19998887777",
        )

        self.payment = Payment.objects.create(
            tenant=self.tenant,
            client=self.client_user,
            amount=Decimal("50.00"),
            status="completed",
        )
        self.event = GHLIntegrationService.dispatch_purchase_event(payment=self.payment)

    @patch("apps.ghl.client.GHLClient._request")
    def test_non_retryable_client_error_marks_failed(self, mock_request):
        mock_request.side_effect = GHLApiError("Invalid payload schema", status_code=422)

        deliver_ghl_outbox_event_task(str(self.event.id))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, GHLOutboxEvent.Status.FAILED)
        self.assertEqual(self.event.response_status_code, 422)

    @patch("apps.ghl.oauth.requests.post")
    @patch("apps.ghl.client.GHLClient._request")
    def test_auth_error_refreshes_token_and_succeeds(self, mock_request, mock_post):
        mock_post_res = MagicMock()
        mock_post_res.status_code = 200
        mock_post_res.json.return_value = {
            "access_token": "new_refreshed_tok",
            "refresh_token": "new_refreshed_ref",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_post_res

        call_count = {"count": 0}
        def mock_side_effect(*args, **kwargs):
            call_count["count"] += 1
            if call_count["count"] == 1:
                raise GHLAuthError("Expired token", status_code=401)
            return {"contacts": [], "contact": {"id": "recovered_contact"}}

        mock_request.side_effect = mock_side_effect

        deliver_ghl_outbox_event_task(str(self.event.id))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, GHLOutboxEvent.Status.DELIVERED)
        self.conn.refresh_from_db()
        self.assertEqual(self.conn.access_token, "new_refreshed_tok")
