import logging
import uuid
from typing import Any
from django.db import transaction
from django.utils import timezone

from apps.core.tenants.models import Tenant
from apps.ghl.client import GHLClient, GHLApiError, GHLAuthError
from apps.ghl.models import GHLOutboxEvent, TenantGHLConnection
from apps.ghl.oauth import refresh_connection_tokens

logger = logging.getLogger(__name__)


class GHLIntegrationService:
    @staticmethod
    def get_connection_for_tenant(tenant: Tenant) -> TenantGHLConnection | None:
        """
        Retrieves active GoHighLevel connection for the specified tenant.
        """
        if not tenant:
            return None
        return TenantGHLConnection.all_objects.filter(
            tenant=tenant,
            status=TenantGHLConnection.Status.CONNECTED
        ).first()

    @classmethod
    def get_client_for_tenant(cls, tenant: Tenant) -> GHLClient | None:
        """
        Returns an authenticated GHLClient for the tenant.
        Refreshes access token automatically if expired.
        """
        connection = cls.get_connection_for_tenant(tenant)
        if not connection or not connection.location_id or not connection.access_token:
            return None

        # Automatically refresh expired token
        if connection.is_token_expired and connection.refresh_token:
            try:
                connection = refresh_connection_tokens(connection)
            except Exception as e:
                logger.error("Failed to auto-refresh GHL token for tenant %s: %s", tenant.id, e)
                return None

        return GHLClient(
            access_token=connection.access_token,
            location_id=connection.location_id,
        )

    @classmethod
    def dispatch_purchase_event(
        cls,
        session=None,
        package=None,
        payment=None,
        customer_info: dict[str, Any] | None = None,
    ) -> GHLOutboxEvent | None:
        """
        Dispatches a successful package purchase event to the tenant's connected GoHighLevel account.
        Derives the tenant strictly from persisted records to ensure strict tenant isolation.
        Guarantees idempotency and transactional outbox persistence.
        """
        # 1. Derive tenant strictly from trusted database records
        tenant = (
            getattr(session, 'tenant', None)
            or getattr(payment, 'tenant', None)
            or getattr(package, 'tenant', None)
        )
        if not tenant:
            logger.warning("GHL dispatch aborted: No tenant associated with purchase record.")
            return None

        # 2. Check if tenant has a connected GHL location
        connection = cls.get_connection_for_tenant(tenant)
        if not connection:
            logger.debug("Skipping GHL purchase sync: Tenant %s has no active GHL connection.", tenant.id)
            return None

        # 3. Only synchronize successful/completed purchases (not pending, failed, or abandoned)
        if payment and getattr(payment, 'status', None) and payment.status != 'completed':
            logger.info("Skipping GHL purchase sync: Payment %s is not completed (status=%s).", payment.id, payment.status)
            return None

        from apps.payments.models import ExternalCheckoutSession
        if session and getattr(session, 'status', None) and session.status != ExternalCheckoutSession.StatusChoices.COMPLETED:
            logger.info("Skipping GHL purchase sync: Session %s is not completed (status=%s).", session.id, session.status)
            return None

        # 4. Extract required fields:
        # - Customer name
        # - Customer email
        # - Customer phone number
        # - Purchased package name
        # - Actual amount paid
        first_name = ""
        last_name = ""
        email = ""
        phone = ""

        if session:
            first_name = (getattr(session, 'first_name', '') or '').strip()
            last_name = (getattr(session, 'last_name', '') or '').strip()
            email = (getattr(session, 'email', '') or '').strip()
            phone = (getattr(session, 'phone_number', '') or '').strip()

        client_user = getattr(package, 'client', None) or getattr(session, 'customer_user', None) or getattr(payment, 'client', None)
        if client_user:
            profile = getattr(client_user, 'profile', None)
            if not first_name:
                first_name = (getattr(profile, 'first_name', '') or getattr(client_user, 'first_name', '') or '').strip()
            if not last_name:
                last_name = (getattr(profile, 'last_name', '') or getattr(client_user, 'last_name', '') or '').strip()
            if not email:
                email = (getattr(client_user, 'email', '') or '').strip()
            if not phone:
                phone = (getattr(profile, 'phone_number', '') or getattr(client_user, 'phone_number', '') or '').strip()

        if customer_info:
            first_name = first_name or customer_info.get('first_name', '').strip()
            last_name = last_name or customer_info.get('last_name', '').strip()
            email = email or customer_info.get('email', '').strip()
            phone = phone or customer_info.get('phone', '').strip()

        customer_name = f"{first_name} {last_name}".strip()
        if not customer_name and client_user and hasattr(client_user, 'get_full_name'):
            customer_name = client_user.get_full_name().strip()
        if not customer_name:
            customer_name = email

        # Package name
        package_name = "Package"
        if package and getattr(package, 'package_type', None):
            package_name = package.package_type.name
        elif session and getattr(session, 'package_type', None):
            package_name = session.package_type.name
        elif getattr(payment, 'package_type', None):
            package_name = payment.package_type.name

        # Actual amount paid
        amount_paid = 0.0
        if payment and getattr(payment, 'amount', None) is not None:
            amount_paid = float(payment.amount)
        elif session and getattr(session, 'price_snapshot', None) is not None:
            amount_paid = float(session.price_snapshot)

        currency = (
            getattr(session, 'currency', None)
            or getattr(tenant, 'currency', None)
            or 'USD'
        ).upper()

        # 5. Idempotency check: prevent duplicate purchase processing
        payment_ref_raw = (
            str(getattr(payment, 'id', ''))
            or str(getattr(session, 'id', ''))
            or str(getattr(package, 'id', ''))
        )
        payment_ref_key = f"purchase_{payment_ref_raw}"

        existing_event = GHLOutboxEvent.all_objects.filter(
            tenant=tenant,
            payment_ref=payment_ref_key
        ).first()
        if existing_event:
            logger.info("GHL purchase event already exists for payment ref %s (event_id=%s)", payment_ref_key, existing_event.event_id)
            return existing_event

        # 6. Create outbox event
        clean_event_id = f"ghl_evt_{uuid.uuid4().hex}"
        payload = {
            "customer_name": customer_name,
            "customer_first_name": first_name,
            "customer_last_name": last_name,
            "customer_email": email,
            "customer_phone": phone,
            "package_name": package_name,
            "amount_paid": amount_paid,
            "currency": currency,
            "payment_id": str(getattr(payment, 'id', '') or ''),
            "package_id": str(getattr(package, 'id', '') or ''),
            "purchased_at": timezone.now().isoformat(),
        }

        outbox_event = GHLOutboxEvent.all_objects.create(
            tenant=tenant,
            event_id=clean_event_id,
            payment_ref=payment_ref_key,
            event_type='package.purchased',
            occurred_at=timezone.now(),
            payload=payload,
            status=GHLOutboxEvent.Status.PENDING,
        )

        # 7. Queue Celery task on database transaction commit
        from apps.ghl.tasks import deliver_ghl_outbox_event_task
        eid_str = str(outbox_event.id)
        transaction.on_commit(lambda: deliver_ghl_outbox_event_task.delay(eid_str))

        logger.info("Queued GHL purchase outbox event %s for tenant %s (payment_ref=%s)", clean_event_id, tenant.id, payment_ref_key)
        return outbox_event

    @classmethod
    def sync_purchase_to_ghl(cls, event: GHLOutboxEvent) -> dict[str, Any]:
        """
        Synchronizes a purchase event to GoHighLevel:
        1. Searches for existing contact in the tenant's GHL location using email and/or phone.
        2. Updates the existing contact or creates a new contact if not found.
        3. Associates the package name and actual amount paid via tags and notes.
        4. Strictly scoped to the event's tenant GHL location.
        """
        tenant = event.tenant
        connection = cls.get_connection_for_tenant(tenant)
        if not connection:
            raise GHLApiError(f"No active GHL connection found for tenant {tenant.id}")

        client = cls.get_client_for_tenant(tenant)
        if not client:
            raise GHLApiError(f"Unable to initialize GHL client for tenant {tenant.id}")

        payload = event.payload or {}
        email = payload.get("customer_email")
        phone = payload.get("customer_phone")
        first_name = payload.get("customer_first_name", "")
        last_name = payload.get("customer_last_name", "")
        full_name = payload.get("customer_name", "")
        package_name = payload.get("package_name", "Package")
        amount_paid = float(payload.get("amount_paid", 0.0))
        currency = payload.get("currency", "USD")

        package_tag = f"Package: {package_name}"
        amount_tag = f"Paid: {currency} {amount_paid:.2f}"
        purchase_note = f"Fitverx Checkout Purchase:\nPackage: {package_name}\nAmount Paid: {currency} {amount_paid:.2f}"

        # 1. Search for existing contact in connected GHL location
        existing_contact = client.lookup_contact(email=email, phone=phone)

        if existing_contact:
            contact_id = existing_contact.get("id")
            current_tags = existing_contact.get("tags") or []
            merged_tags = list(set(current_tags + [package_tag, amount_tag]))

            update_data: dict[str, Any] = {
                "tags": merged_tags,
            }
            if first_name and not existing_contact.get("firstName"):
                update_data["firstName"] = first_name
            if last_name and not existing_contact.get("lastName"):
                update_data["lastName"] = last_name
            if phone and not existing_contact.get("phone"):
                update_data["phone"] = phone

            update_res = client.update_contact(contact_id, update_data)
            try:
                client.add_contact_note(contact_id, purchase_note)
            except Exception as note_exc:
                logger.warning("Failed to add note to GHL contact %s: %s", contact_id, note_exc)

            logger.info("Successfully updated existing GHL contact %s for tenant %s purchase", contact_id, tenant.id)
            return {
                "action": "updated",
                "contact_id": contact_id,
                "response": update_res,
            }
        else:
            create_data: dict[str, Any] = {
                "name": full_name or f"{first_name} {last_name}".strip() or email,
                "email": email,
                "phone": phone,
                "tags": [package_tag, amount_tag],
            }
            if first_name:
                create_data["firstName"] = first_name
            if last_name:
                create_data["lastName"] = last_name

            create_res = client.create_contact(create_data)
            contact_obj = create_res.get("contact") if isinstance(create_res.get("contact"), dict) else create_res
            contact_id = contact_obj.get("id")

            if contact_id:
                try:
                    client.add_contact_note(contact_id, purchase_note)
                except Exception as note_exc:
                    logger.warning("Failed to add note to new GHL contact %s: %s", contact_id, note_exc)

            logger.info("Successfully created new GHL contact %s for tenant %s purchase", contact_id, tenant.id)
            return {
                "action": "created",
                "contact_id": contact_id,
                "response": create_res,
            }
