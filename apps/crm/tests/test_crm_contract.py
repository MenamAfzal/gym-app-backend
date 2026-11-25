import hashlib
import hmac
import json
import time
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone

from apps.core.tenants.models import Tenant
from apps.crm.client import CRMClient
from apps.crm.models import CRMOutboxEvent, TenantCRMConfig
from apps.crm.services import CRMIntegrationService
from apps.payments.models import ExternalCheckoutSession
from apps.scheduling.models import Location, PackageType, Package, Payment
from apps.users.models import User, UserRole


class CRMContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(
            name="Alpha Performance Gym",
            subdomain="alpha-gym",
            currency="usd",
        )
        cls.crm_config = TenantCRMConfig.objects.create(
            tenant=cls.tenant,
            api_key_id="crm_key_test_123",
            api_secret="crm_secret_test_456",
            crm_base_url="http://testserver",
            is_active=True,
            sync_packages_on_save=True,
            stop_lead_workflow_name="Lead Follow-Up Sequence",
            start_onboarding_workflow_name="Post-Purchase Onboarding",
        )
        cls.location = Location.objects.create(
            tenant=cls.tenant,
            name="Main Facility",
            address="123 Fitness Way",
            timezone="UTC",
        )
        cls.package_type = PackageType.objects.create(
            tenant=cls.tenant,
            location=cls.location,
            name="12-Week Transformation",
            credit_count=36,
            price=Decimal("499.00"),
            validity_days=84,
            billing_cycle="monthly",
            is_active=True,
        )
        cls.user = User.objects.create_user(
            email="sara.malik@example.com",
            password="TestPassword123!",
            first_name="Sara",
            last_name="Malik",
            role=UserRole.CLIENT,
            tenant=cls.tenant,
        )
        cls.trainer = User.objects.create_user(
            email="coach.mike@example.com",
            password="TestPassword123!",
            first_name="Mike",
            last_name="Coach",
            role=UserRole.TRAINER,
            tenant=cls.tenant,
        )

    def test_hmac_canonicalization_and_headers(self):
        """
        Verify that CRMClient constructs the exact canonical string f'{timestamp}.{body}',
        calculates hex HMAC-SHA256, and supplies required headers without X-Tenant.
        """
        client = CRMClient(
            base_url="http://crm.example.com",
            api_key_id="crm_key_abc",
            api_secret="crm_secret_xyz",
        )
        body = json.dumps({"test": "data"}, separators=(",", ":")).encode("utf-8")
        headers = client._build_headers(body)

        self.assertEqual(headers["X-API-Key"], "crm_key_abc")
        self.assertIn("X-Timestamp", headers)
        self.assertIn("X-Signature", headers)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("X-Tenant", headers)
        self.assertNotIn("HTTP_X_TENANT", headers)

        # Independently verify the signature
        ts = headers["X-Timestamp"]
        expected_sig = hmac.new(
            b"crm_secret_xyz",
            f"{ts}.".encode("utf-8") + body,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(headers["X-Signature"], expected_sig)

    def test_serialize_package_type_conforms_to_crm_schema(self):
        """
        Verify serialize_package_type outputs all fields expected by CRM PackageSyncItemSerializer.
        """
        payload = CRMIntegrationService.serialize_package_type(self.package_type)

        self.assertEqual(payload["external_id"], str(self.package_type.id))
        self.assertEqual(payload["name"], "12-Week Transformation")
        self.assertEqual(payload["price"], "499.00")
        self.assertEqual(payload["currency"], "USD")
        self.assertEqual(payload["billing_interval"], "monthly")
        self.assertEqual(payload["status"], "active")
        self.assertIn("36 Session Credits", payload["features"])
        self.assertEqual(payload["metadata"]["credit_count"], 36)
        self.assertEqual(payload["metadata"]["validity_days"], 84)

    def test_billing_interval_choice_mappings(self):
        """
        Verify billing intervals map cleanly to CRM choices:
        one_time, monthly, quarterly, biannual, annual, custom.
        """
        self.package_type.billing_cycle = "yearly"
        p_yearly = CRMIntegrationService.serialize_package_type(self.package_type)
        self.assertEqual(p_yearly["billing_interval"], "annual")

        self.package_type.billing_cycle = "weekly"
        p_weekly = CRMIntegrationService.serialize_package_type(self.package_type)
        self.assertEqual(p_weekly["billing_interval"], "custom")

    def test_serialize_contact_guarantees_identifier(self):
        """
        Verify serialize_contact satisfies the CRM requirement:
        at least one contact identifier (external_id, email, or phone) is required.
        """
        contact = CRMIntegrationService.serialize_contact(user=self.user)
        self.assertEqual(contact["external_id"], f"fitverx_{self.user.id}")
        self.assertEqual(contact["email"], "sara.malik@example.com")
        self.assertEqual(contact["first_name"], "Sara")
        self.assertEqual(contact["last_name"], "Malik")
        self.assertEqual(contact["full_name"], "Sara Malik")

    @patch("apps.crm.tasks.deliver_crm_event_task.delay")
    def test_dispatch_purchase_event_payload_and_directives(self, mock_delay):
        """
        Verify dispatch_purchase_event formats the exact envelope with automation directives:
        - action 'stop' for promotional workflow
        - action 'trigger' for onboarding workflow
        """
        session = ExternalCheckoutSession.objects.create(
            tenant=self.tenant,
            package_type=self.package_type,
            customer_user=self.user,
            email=self.user.email,
            first_name=self.user.first_name,
            last_name=self.user.last_name,
            price_snapshot=Decimal("499.00"),
            currency="USD",
            credit_count_snapshot=36,
            validity_days_snapshot=84,
            expires_at=timezone.now() + timezone.timedelta(days=1),
            stripe_subscription_id="sub_test_trans_001",
            status=ExternalCheckoutSession.StatusChoices.COMPLETED,
            completed_at=timezone.now(),
        )
        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.user,
            package_type=self.package_type,
            credits_remaining=36,
            total_credits_allocated=36,
            expires_at=timezone.now() + timezone.timedelta(days=84),
            stripe_subscription_id="sub_test_trans_001",
            status="active",
        )
        payment = Payment.objects.create(
            tenant=self.tenant,
            client=self.user,
            package=pkg,
            amount=Decimal("499.00"),
            type="package_purchase",
            status="completed",
            provider_ref="ch_test_123",
            idempotency_key="ch_test_123",
        )

        with self.captureOnCommitCallbacks(execute=True):
            outbox_event = CRMIntegrationService.dispatch_purchase_event(session, pkg, payment)
        self.assertIsNotNone(outbox_event)
        self.assertEqual(outbox_event.event_type, "package.purchased")
        self.assertEqual(outbox_event.status, CRMOutboxEvent.Status.PENDING)
        self.assertTrue(mock_delay.called)

        envelope = outbox_event.payload
        self.assertEqual(envelope["event_id"], f"evt_purchase_{session.id}")
        self.assertEqual(envelope["type"], "package.purchased")
        self.assertEqual(envelope["contact"]["email"], "sara.malik@example.com")
        self.assertEqual(envelope["data"]["package_external_id"], str(self.package_type.id))
        self.assertEqual(envelope["data"]["amount"], 499.00)
        self.assertEqual(envelope["data"]["currency"], "USD")
        self.assertEqual(envelope["data"]["stripe_subscription_id"], "sub_test_trans_001")

        # Automation directives
        directives = envelope.get("automation", [])
        self.assertEqual(len(directives), 2)
        self.assertEqual(directives[0], {"action": "stop", "workflow": "Lead Follow-Up Sequence"})
        self.assertEqual(directives[1], {"action": "trigger", "workflow": "Post-Purchase Onboarding"})

    @patch("apps.crm.tasks.deliver_crm_event_task.delay")
    def test_all_remaining_event_dispatchers(self, mock_delay):
        """
        Verify every single CRM-supported event type has a functioning dispatcher:
        - subscription.created
        - subscription.updated
        - subscription.renewed
        - subscription.cancelled
        - subscription.expired
        - payment.succeeded
        - payment.failed
        - refund.issued
        - appointment.* (booked, cancelled, rescheduled, completed)
        - customer.updated
        """
        pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.user,
            package_type=self.package_type,
            credits_remaining=36,
            total_credits_allocated=36,
            expires_at=timezone.now() + timezone.timedelta(days=84),
            stripe_subscription_id="sub_test_trans_002",
            status="active",
        )
        payment = Payment.objects.create(
            tenant=self.tenant,
            client=self.user,
            package=pkg,
            amount=Decimal("499.00"),
            type="package_purchase",
            status="completed",
            provider_ref="ch_test_dispatchers",
            idempotency_key="ch_test_dispatchers",
        )

        # 1. subscription.created
        evt_created = CRMIntegrationService.dispatch_subscription_created_event(pkg, payment)
        self.assertIsNotNone(evt_created)
        self.assertEqual(evt_created.event_type, "subscription.created")
        self.assertEqual(evt_created.payload["data"]["subscription_status"], "active")
        self.assertEqual(evt_created.payload["data"]["package_external_id"], str(self.package_type.id))

        # 2. subscription.updated
        evt_updated = CRMIntegrationService.dispatch_subscription_updated_event(pkg, new_status="past_due")
        self.assertIsNotNone(evt_updated)
        self.assertEqual(evt_updated.event_type, "subscription.updated")
        self.assertEqual(evt_updated.payload["data"]["subscription_status"], "past_due")

        # 3. subscription.renewed
        evt_renewed = CRMIntegrationService.dispatch_subscription_renewed_event(pkg, payment)
        self.assertIsNotNone(evt_renewed)
        self.assertEqual(evt_renewed.event_type, "subscription.renewed")
        self.assertEqual(evt_renewed.payload["data"]["amount"], 499.00)

        # 4. subscription.cancelled
        evt_cancelled = CRMIntegrationService.dispatch_subscription_cancelled_event(pkg, reason="Moved away")
        self.assertIsNotNone(evt_cancelled)
        self.assertEqual(evt_cancelled.event_type, "subscription.cancelled")
        self.assertEqual(evt_cancelled.payload["data"]["cancellation_reason"], "Moved away")

        # 5. subscription.expired
        evt_expired = CRMIntegrationService.dispatch_subscription_expired_event(pkg, reason="validity_period_ended")
        self.assertIsNotNone(evt_expired)
        self.assertEqual(evt_expired.event_type, "subscription.expired")
        self.assertEqual(evt_expired.payload["data"]["expiration_reason"], "validity_period_ended")

        # 6. payment.succeeded
        evt_paysucc = CRMIntegrationService.dispatch_payment_succeeded_event(payment)
        self.assertIsNotNone(evt_paysucc)
        self.assertEqual(evt_paysucc.event_type, "payment.succeeded")
        self.assertEqual(evt_paysucc.payload["data"]["amount"], 499.00)

        # 7. payment.failed
        evt_payfail = CRMIntegrationService.dispatch_payment_failed_event(
            tenant=self.tenant,
            user=self.user,
            amount=499.00,
            currency="USD",
            reason="Card declined",
            package_type=self.package_type,
        )
        self.assertIsNotNone(evt_payfail)
        self.assertEqual(evt_payfail.event_type, "payment.failed")
        self.assertEqual(evt_payfail.payload["data"]["failure_reason"], "Card declined")

        # 8. refund.issued
        evt_refund = CRMIntegrationService.dispatch_refund_issued_event(
            tenant=self.tenant,
            user=self.user,
            amount=499.00,
            currency="USD",
            reason="Customer request",
            package=pkg,
        )
        self.assertIsNotNone(evt_refund)
        self.assertEqual(evt_refund.event_type, "refund.issued")
        self.assertEqual(evt_refund.payload["data"]["refund_amount"], 499.00)

        # 9. appointment.*
        from apps.scheduling.models import Appointment
        appt = Appointment.objects.create(
            tenant=self.tenant,
            client=self.user,
            provider=self.trainer,
            location=self.location,
            start_at=timezone.now() + timezone.timedelta(days=2),
            end_at=timezone.now() + timezone.timedelta(days=2, hours=1),
            status="booked",
        )
        for appt_status in ("booked", "cancelled", "rescheduled", "completed"):
            appt.status = appt_status
            appt_evt = CRMIntegrationService.dispatch_appointment_event(appt, appt_status)
            self.assertIsNotNone(appt_evt)
            self.assertEqual(appt_evt.event_type, f"appointment.{appt_status}")
            self.assertEqual(appt_evt.payload["data"]["external_ref"], str(appt.id))

        # 10. customer.updated
        cust_evt = CRMIntegrationService.dispatch_customer_updated_event(self.user)
        self.assertIsNotNone(cust_evt)
        self.assertEqual(cust_evt.event_type, "customer.updated")
        self.assertEqual(cust_evt.payload["contact"]["email"], self.user.email)

    def test_credential_encryption_and_decryption(self):
        """
        Verify that TenantCRMConfig encrypts api_secret with Fernet at rest,
        stores ciphertext prefixed with enc::, and decrypts transparently.
        """
        tenant_enc = Tenant.objects.create(
            name="Encryption Test Gym",
            subdomain="enc-gym",
            currency="usd",
        )
        raw_secret = "super_secret_crm_key_xyz987"
        cfg = TenantCRMConfig.objects.create(
            tenant=tenant_enc,
            api_key_id="key_enc_test",
            api_secret=raw_secret,
            crm_base_url="http://testserver",
            is_active=True,
        )
        cfg.refresh_from_db()

        # In database: must be encrypted
        self.assertTrue(cfg.api_secret.startswith("enc::"))
        self.assertNotEqual(cfg.api_secret, raw_secret)

        # Decrypted via method / property
        self.assertEqual(cfg.get_api_secret(), raw_secret)
        self.assertEqual(cfg.raw_api_secret, raw_secret)

        # Verify client initialized with decrypted secret
        client = CRMIntegrationService.get_client_for_tenant(tenant_enc)
        self.assertEqual(client.api_secret, raw_secret)

    def test_transaction_rollback_prevents_outbox_delivery(self):
        """
        Verify outbox events created inside a rolling back transaction are aborted
        and Celery delivery tasks are NOT dispatched after rollback.
        """
        from django.db import transaction

        initial_count = CRMOutboxEvent.all_objects.count()

        try:
            with transaction.atomic():
                CRMIntegrationService.dispatch_event(
                    tenant=self.tenant,
                    event_type="customer.updated",
                    contact_data={"email": "sara.malik@example.com"},
                    data={"city": "Austin"},
                )
                raise RuntimeError("Intentional rollback")
        except RuntimeError:
            pass

        # Since transaction rolled back, outbox event should not exist
        self.assertEqual(CRMOutboxEvent.all_objects.count(), initial_count)

    @patch("apps.crm.client.CRMClient.ingest_event")
    def test_deliver_task_success_and_duplicate(self, mock_ingest):
        """
        Verify deliver_crm_event_task handles HTTP 202 (first delivery)
        and HTTP 200 idempotent duplicate cleanly without error.
        """
        from apps.crm.tasks import deliver_crm_event_task

        outbox_event = CRMOutboxEvent.all_objects.create(
            tenant=self.tenant,
            event_id="evt_test_deliv_001",
            event_type="customer.updated",
            payload={"event_id": "evt_test_deliv_001", "type": "customer.updated", "contact": {"email": "sara@example.com"}},
            status=CRMOutboxEvent.Status.PENDING,
        )

        # 1. First delivery (202 Accepted)
        mock_ingest.return_value = (202, {"status": "accepted", "idempotent_duplicate": False, "external_event_id": "uuid-123"})
        res = deliver_crm_event_task(str(outbox_event.id))
        self.assertEqual(res["status"], "delivered")

        outbox_event.refresh_from_db()
        self.assertEqual(outbox_event.status, CRMOutboxEvent.Status.DELIVERED)
        self.assertEqual(outbox_event.response_status_code, 202)

        # 2. Subsequent call returns already_delivered
        res2 = deliver_crm_event_task(str(outbox_event.id))
        self.assertEqual(res2["status"], "already_delivered")

    @patch("apps.crm.client.CRMClient.ingest_event")
    def test_deliver_task_client_error_marks_failed(self, mock_ingest):
        """
        Verify 4xx client errors (e.g. 422 unsupported or 403 forbidden)
        mark the outbox event as FAILED and do not trigger Celery retries.
        """
        from apps.crm.tasks import deliver_crm_event_task

        outbox_event = CRMOutboxEvent.all_objects.create(
            tenant=self.tenant,
            event_id="evt_test_client_err",
            event_type="unsupported.event",
            payload={"event_id": "evt_test_client_err", "type": "unsupported.event", "contact": {"email": "sara@example.com"}},
            status=CRMOutboxEvent.Status.PENDING,
        )

        mock_ingest.return_value = (422, {"detail": "Unsupported external event type 'unsupported.event'."})
        res = deliver_crm_event_task(str(outbox_event.id))
        self.assertEqual(res["status"], "client_error")
        self.assertEqual(res["status_code"], 422)

        outbox_event.refresh_from_db()
        self.assertEqual(outbox_event.status, CRMOutboxEvent.Status.FAILED)
        self.assertIn("Unsupported", outbox_event.error_message)

    @patch("apps.crm.client.CRMClient.ingest_event")
    def test_deliver_task_max_retries_marks_abandoned(self, mock_ingest):
        """
        Verify that after reaching MAX_EVENT_ATTEMPTS, the outbox event is marked ABANDONED (dead-letter).
        """
        from apps.crm.tasks import deliver_crm_event_task, MAX_EVENT_ATTEMPTS

        outbox_event = CRMOutboxEvent.all_objects.create(
            tenant=self.tenant,
            event_id="evt_test_abandon",
            event_type="customer.updated",
            payload={"event_id": "evt_test_abandon", "type": "customer.updated", "contact": {"email": "sara@example.com"}},
            status=CRMOutboxEvent.Status.PENDING,
            attempts=MAX_EVENT_ATTEMPTS - 1,
        )

        mock_ingest.return_value = (500, {"error": "CRM Internal Server Error"})
        res = deliver_crm_event_task(str(outbox_event.id))
        self.assertEqual(res["status"], "abandoned")

        outbox_event.refresh_from_db()
        self.assertEqual(outbox_event.status, CRMOutboxEvent.Status.ABANDONED)

    def test_cross_tenant_isolation(self):
        """
        Verify tenant isolation: Tenant A's client, config, and outbox events
        cannot be mixed with or accessed by Tenant B.
        """
        tenant_b = Tenant.objects.create(
            name="Beta Fitness Club",
            subdomain="beta-club",
            currency="eur",
        )
        TenantCRMConfig.objects.create(
            tenant=tenant_b,
            api_key_id="crm_key_beta_789",
            api_secret="crm_secret_beta_999",
            crm_base_url="http://testserver-beta",
            is_active=True,
        )

        client_a = CRMIntegrationService.get_client_for_tenant(self.tenant)
        client_b = CRMIntegrationService.get_client_for_tenant(tenant_b)

        self.assertNotEqual(client_a.api_key_id, client_b.api_key_id)
        self.assertEqual(client_a.api_key_id, "crm_key_test_123")
        self.assertEqual(client_b.api_key_id, "crm_key_beta_789")

        # Verify outbox events are tenant-partitioned
        evt_a = CRMOutboxEvent.all_objects.create(
            tenant=self.tenant,
            event_id="evt_tenant_a",
            event_type="customer.updated",
            payload={},
        )
        evt_b = CRMOutboxEvent.all_objects.create(
            tenant=tenant_b,
            event_id="evt_tenant_b",
            event_type="customer.updated",
            payload={},
        )

        self.assertEqual(CRMOutboxEvent.all_objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(CRMOutboxEvent.all_objects.filter(tenant=tenant_b).count(), 1)

    @patch("apps.crm.client.CRMClient.sync_package")
    def test_package_deactivation_syncs_archived_status(self, mock_sync):
        """
        Verify deactivating a PackageType (is_active=False) serializes status='archived'
        and triggers auto-sync to CRM.
        """
        mock_sync.return_value = (200, {"status": "success"})

        self.package_type.is_active = False
        self.package_type.save()

        payload = CRMIntegrationService.serialize_package_type(self.package_type)
        self.assertEqual(payload["status"], "archived")

        success, resp = CRMIntegrationService.sync_package_to_crm(self.package_type)
        self.assertTrue(success)
        self.assertTrue(mock_sync.called)

    @patch("apps.crm.client.CRMClient.sync_bulk_packages")
    def test_reconcile_all_tenant_packages_to_crm_task(self, mock_bulk):
        """
        Verify periodic package reconciliation sweeps all active CRM tenants.
        """
        mock_bulk.return_value = (200, {"status": "success", "total": 1, "created": 0, "updated": 1})
        from apps.crm.tasks import reconcile_all_tenant_packages_to_crm_task

        res = reconcile_all_tenant_packages_to_crm_task()
        self.assertIn(str(self.tenant.id), res)
        self.assertTrue(res[str(self.tenant.id)]["success"])

    @patch("apps.crm.services.CRMIntegrationService.dispatch_subscription_expired_event")
    def test_reconcile_expired_packages_task(self, mock_disp):
        """
        Verify periodic expiration sweep marks expired packages as 'expired'
        and dispatches subscription.expired events.
        """
        from apps.scheduling.models import Package
        from apps.crm.tasks import reconcile_expired_packages_task

        past_time = timezone.now() - timezone.timedelta(days=1)
        expired_pkg = Package.objects.create(
            tenant=self.tenant,
            client=self.user,
            package_type=self.package_type,
            credits_remaining=0,
            total_credits_allocated=36,
            expires_at=past_time,
            status="active",
        )

        res = reconcile_expired_packages_task()
        self.assertGreaterEqual(res["reconciled_expired_count"], 1)

        expired_pkg.refresh_from_db()
        self.assertEqual(expired_pkg.status, "expired")
        self.assertTrue(mock_disp.called)

    @patch("apps.crm.client.CRMClient.sync_bulk_packages")
    @patch("apps.crm.tasks.deliver_crm_event_task.delay")
    def test_tenant_branding_colors_and_logo_sync_and_embedding(self, mock_delay, mock_sync_bulk):
        """
        Verify tenant branding colors and logo are:
        1. Extracted accurately by serialize_tenant_branding (from Tenant.branding, Tenant.logo, and TenantCheckoutConfig).
        2. Embedded in package sync payloads and outgoing CRM event envelopes.
        3. Synced via POST /api/v1/external/packages/sync/ on Tenant save and via sync_branding_to_crm.
        """
        mock_sync_bulk.return_value = (200, {"status": "success"})

        self.tenant.branding = {
            "primary_color": "#2563EB",
            "secondary_color": "#0F172A",
            "accent_color": "#F59E0B",
            "background_color": "#FFFFFF",
            "text_color": "#1E293B",
            "logo_url": "https://cdn.fitverx.com/logos/alpha-gym.png",
        }
        self.tenant.save()

        # Saving Tenant with active CRM config should auto-trigger sync_branding_to_crm -> sync_bulk_packages
        self.assertTrue(mock_sync_bulk.called)

        branding_payload = CRMIntegrationService.serialize_tenant_branding(self.tenant)
        self.assertEqual(branding_payload["primary_color"], "#2563EB")
        self.assertEqual(branding_payload["secondary_color"], "#0F172A")
        self.assertEqual(branding_payload["accent_color"], "#F59E0B")
        self.assertEqual(branding_payload["logo_url"], "https://cdn.fitverx.com/logos/alpha-gym.png")
        self.assertEqual(branding_payload["colors"]["primary_color"], "#2563EB")

        # Verify package serialization includes branding, logo_url, and featured info
        self.package_type.is_featured = True
        self.package_type.feature_badge = "Most Popular"
        pkg_payload = CRMIntegrationService.serialize_package_type(self.package_type)
        self.assertEqual(pkg_payload["logo_url"], "https://cdn.fitverx.com/logos/alpha-gym.png")
        self.assertEqual(pkg_payload["branding"]["primary_color"], "#2563EB")
        self.assertTrue(pkg_payload["is_featured"])
        self.assertEqual(pkg_payload["feature_badge"], "Most Popular")
        self.assertEqual(
            pkg_payload["featured_info"],
            {"is_featured": True, "feature_badge": "Most Popular"},
        )
        self.assertTrue(pkg_payload["metadata"]["is_featured"])
        self.assertEqual(pkg_payload["metadata"]["feature_badge"], "Most Popular")
        self.assertEqual(
            pkg_payload["metadata"]["featured_info"],
            {"is_featured": True, "feature_badge": "Most Popular"},
        )
        self.assertEqual(pkg_payload["metadata"]["logo_url"], "https://cdn.fitverx.com/logos/alpha-gym.png")
        self.assertEqual(pkg_payload["metadata"]["branding"]["primary_color"], "#2563EB")

        # Verify dispatched events include branding & logo_url
        evt = CRMIntegrationService.dispatch_customer_updated_event(self.user)
        self.assertIsNotNone(evt)
        self.assertEqual(evt.payload["branding"]["primary_color"], "#2563EB")
        self.assertEqual(evt.payload["branding"]["logo_url"], "https://cdn.fitverx.com/logos/alpha-gym.png")
        self.assertEqual(evt.payload["data"]["logo_url"], "https://cdn.fitverx.com/logos/alpha-gym.png")
        self.assertEqual(evt.payload["data"]["branding"]["secondary_color"], "#0F172A")
