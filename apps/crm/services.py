from decimal import Decimal
import logging
import time
import uuid
from typing import Any
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.tenants.models import Tenant
from apps.crm.client import CRMClient
from apps.crm.models import CRMOutboxEvent, TenantCRMConfig

logger = logging.getLogger(__name__)


class CRMIntegrationService:
    @staticmethod
    def get_config_for_tenant(tenant: Tenant) -> TenantCRMConfig | None:
        """
        Retrieves active CRM configuration for the specified tenant.
        """
        if not tenant:
            return None
        return TenantCRMConfig.all_objects.filter(tenant=tenant, is_active=True).first()

    @classmethod
    def get_client_for_tenant(cls, tenant: Tenant) -> CRMClient | None:
        """
        Returns an initialized CRMClient for the tenant, or None if not configured.
        """
        config = cls.get_config_for_tenant(tenant)
        if not config or not config.is_configured:
            return None
        return CRMClient(
            base_url=config.crm_base_url,
            api_key_id=config.api_key_id,
            api_secret=config.get_api_secret(),
        )

    @staticmethod
    def serialize_package_type(package_type) -> dict[str, Any]:
        """
        Maps a Fitverx PackageType model instance to CRM's PackageSyncItemSerializer schema:
        - external_id: string (UUID of the PackageType)
        - name: string
        - description: string
        - price: decimal string or float
        - currency: uppercase 3-letter currency (e.g. USD)
        - billing_interval: one_time, monthly, quarterly, biannual, annual, or custom
        - status: active, archived, or draft
        - external_url: optional checkout link
        - features: list of feature strings
        - metadata: dict of metadata
        """
        interval_mapping = {
            'monthly': 'monthly',
            'weekly': 'custom',
            'yearly': 'annual',
            'quarterly': 'quarterly',
            'biannual': 'biannual',
            'one_time': 'one_time',
        }
        cycle = str(getattr(package_type, 'billing_cycle', '') or 'monthly').strip().lower()
        crm_interval = interval_mapping.get(cycle, 'custom')

        tenant = getattr(package_type, 'tenant', None)
        currency_code = (getattr(tenant, 'currency', None) or 'USD').strip().upper()

        frontend_url = getattr(settings, 'FRONTEND_CHECKOUT_URL', '') or getattr(settings, 'FRONTEND_URL', '')
        external_url = f"{frontend_url.rstrip('/')}/packages/{package_type.id}" if frontend_url else ""

        price_val = package_type.price
        if isinstance(price_val, Decimal):
            price_val = float(price_val)

        desc = getattr(package_type, 'description', '')
        if not desc:
            desc = f"{package_type.credit_count} credits | Valid for {package_type.validity_days} days"

        features = [
            f"{package_type.credit_count} Session Credits",
            f"{package_type.validity_days} Days Validity",
        ]
        if getattr(package_type, 'feature_badge', None):
            features.append(str(package_type.feature_badge))

        return {
            "external_id": str(package_type.id),
            "name": package_type.name,
            "description": desc,
            "price": f"{price_val:.2f}",
            "currency": currency_code,
            "billing_interval": crm_interval,
            "status": "active" if getattr(package_type, 'is_active', True) else "archived",
            "external_url": external_url,
            "features": features,
            "metadata": {
                "credit_count": package_type.credit_count,
                "validity_days": package_type.validity_days,
                "billing_cycle": cycle,
                "is_featured": getattr(package_type, 'is_featured', False),
            },
        }

    @staticmethod
    def serialize_contact(user=None, **overrides) -> dict[str, Any]:
        """
        Constructs contact payload matching CRM's ExternalEventContactPayloadSerializer.
        Guarantees at least one identifier (external_id, email, phone) is present.
        """
        ext_id = overrides.get("external_id")
        if not ext_id and user:
            ext_id = f"fitverx_{user.id}"

        email = overrides.get("email")
        if not email and user and getattr(user, "email", None):
            email = user.email

        phone = overrides.get("phone")
        if not phone and user and hasattr(user, "phone_number"):
            phone = user.phone_number

        first_name = overrides.get("first_name")
        if not first_name and user and getattr(user, "first_name", None):
            first_name = user.first_name

        last_name = overrides.get("last_name")
        if not last_name and user and getattr(user, "last_name", None):
            last_name = user.last_name

        full_name = overrides.get("full_name")
        if not full_name:
            if first_name or last_name:
                full_name = f"{first_name or ''} {last_name or ''}".strip()
            elif user:
                full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""

        contact_dict: dict[str, Any] = {}
        if ext_id:
            contact_dict["external_id"] = str(ext_id).strip()
        if email:
            contact_dict["email"] = str(email).strip().lower()
        if phone:
            contact_dict["phone"] = str(phone).strip()
        if first_name:
            contact_dict["first_name"] = str(first_name).strip()
        if last_name:
            contact_dict["last_name"] = str(last_name).strip()
        if full_name:
            contact_dict["full_name"] = str(full_name).strip()

        for key in ("city", "country", "timezone", "tags", "custom_fields"):
            if key in overrides and overrides[key] is not None:
                contact_dict[key] = overrides[key]

        return contact_dict

    @classmethod
    def sync_package_to_crm(cls, package_type) -> tuple[bool, dict[str, Any]]:
        """
        Synchronizes a single PackageType to CRM via POST /api/v1/external/packages/.
        """
        tenant = getattr(package_type, 'tenant', None)
        client = cls.get_client_for_tenant(tenant)
        if not client:
            return False, {"detail": "CRM not configured for this tenant"}

        payload = cls.serialize_package_type(package_type)
        status_code, resp = client.sync_package(payload)
        success = status_code in (200, 201)
        if not success:
            logger.warning("CRM package sync failed for package %s: %s (status %s)", package_type.id, resp, status_code)
        return success, resp

    @classmethod
    def sync_all_packages_to_crm(cls, tenant: Tenant) -> tuple[bool, dict[str, Any]]:
        """
        Bulk synchronizes all active PackageTypes for a tenant via POST /api/v1/external/packages/sync/.
        """
        client = cls.get_client_for_tenant(tenant)
        if not client:
            return False, {"detail": "CRM not configured for this tenant"}

        from apps.scheduling.models import PackageType
        packages = list(PackageType.all_objects.filter(tenant=tenant, is_active=True))
        if not packages:
            return True, {"status": "success", "total": 0, "created": 0, "updated": 0, "packages": []}

        payloads = [cls.serialize_package_type(p) for p in packages]
        status_code, resp = client.sync_bulk_packages(payloads)
        success = status_code == 200
        if not success:
            logger.warning("CRM bulk package sync failed for tenant %s: %s (status %s)", tenant.id, resp, status_code)
        return success, resp

    @classmethod
    def dispatch_event(
        cls,
        *,
        tenant: Tenant,
        event_type: str,
        contact_data: dict[str, Any],
        data: dict[str, Any] | None = None,
        automation_directives: list[dict[str, Any]] | None = None,
        event_id: str | None = None,
        occurred_at: Any = None,
    ) -> CRMOutboxEvent | None:
        """
        Creates a CRMOutboxEvent and enqueues delivery via Celery task.
        """
        config = cls.get_config_for_tenant(tenant)
        if not config or not config.is_configured:
            logger.debug("Skipping CRM event dispatch: CRM not configured for tenant %s", tenant.id)
            return None

        clean_event_id = (event_id or f"evt_{uuid.uuid4().hex}").strip()
        now = occurred_at or timezone.now()
        occurred_iso = now.isoformat() if hasattr(now, "isoformat") else str(now)

        envelope: dict[str, Any] = {
            "event_id": clean_event_id,
            "type": event_type,
            "occurred_at": occurred_iso,
            "contact": contact_data,
            "data": data or {},
        }
        if automation_directives:
            envelope["automation"] = automation_directives

        outbox_event = CRMOutboxEvent.all_objects.create(
            tenant=tenant,
            event_id=clean_event_id,
            event_type=event_type,
            occurred_at=now if hasattr(now, "tzinfo") else timezone.now(),
            payload=envelope,
            status=CRMOutboxEvent.Status.PENDING,
        )

        from apps.crm.tasks import deliver_crm_event_task
        eid_str = str(outbox_event.id)
        transaction.on_commit(lambda: deliver_crm_event_task.delay(eid_str))
        return outbox_event

    @classmethod
    def dispatch_purchase_event(cls, session, package, payment) -> CRMOutboxEvent | None:
        """
        Dispatches package.purchased event to CRM upon checkout completion.
        Includes automation directives to stop lead follow-up sequence and trigger post-purchase onboarding.
        """
        tenant = session.tenant
        config = cls.get_config_for_tenant(tenant)
        if not config or not config.is_configured:
            return None

        user = getattr(session, "customer_user", None) or getattr(package, "client", None)
        contact_data = cls.serialize_contact(
            user=user,
            email=session.email,
            phone=session.phone_number,
            first_name=session.first_name,
            last_name=session.last_name,
        )

        package_type = session.package_type
        price_val = float(session.price_snapshot) if session.price_snapshot is not None else 0.0

        data_payload: dict[str, Any] = {
            "package_external_id": str(package_type.id),
            "package": package_type.name,
            "plan": package_type.name,
            "amount": price_val,
            "currency": (session.currency or "USD").upper(),
            "purchased_at": (session.completed_at or timezone.now()).isoformat(),
            "payment_id": str(payment.id),
            "package_id": str(package.id),
        }
        if session.stripe_subscription_id:
            data_payload["stripe_subscription_id"] = session.stripe_subscription_id
            data_payload["subscription_status"] = "active"

        automation: list[dict[str, Any]] = []
        if config.stop_lead_workflow_name:
            automation.append({
                "action": "stop",
                "workflow": config.stop_lead_workflow_name,
            })
        if config.start_onboarding_workflow_name:
            automation.append({
                "action": "trigger",
                "workflow": config.start_onboarding_workflow_name,
            })

        event_id = f"evt_purchase_{session.id}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type="package.purchased",
            contact_data=contact_data,
            data=data_payload,
            automation_directives=automation,
            event_id=event_id,
            occurred_at=session.completed_at or timezone.now(),
        )

    @classmethod
    def dispatch_subscription_created_event(cls, package, payment=None) -> CRMOutboxEvent | None:
        """
        Dispatches subscription.created event to CRM when a recurring package subscription is created.
        """
        tenant = package.tenant
        client_user = package.client
        contact_data = cls.serialize_contact(user=client_user)

        amount_val = float(payment.amount) if payment and payment.amount else float(package.package_type.price or 0.0)
        data_payload: dict[str, Any] = {
            "package_external_id": str(package.package_type_id),
            "package": package.package_type.name,
            "plan": package.package_type.name,
            "amount": amount_val,
            "currency": (getattr(tenant, "currency", None) or "USD").upper(),
            "subscription_status": package.status or "active",
            "created_at": (package.created_at or timezone.now()).isoformat(),
            "stripe_subscription_id": package.stripe_subscription_id or "",
            "package_id": str(package.id),
        }
        if payment:
            data_payload["payment_id"] = str(payment.id)

        event_id = f"evt_subcreate_{package.stripe_subscription_id or package.id}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type="subscription.created",
            contact_data=contact_data,
            data=data_payload,
            event_id=event_id,
        )

    @classmethod
    def dispatch_subscription_updated_event(cls, package, new_status: str = None, reason: str = "") -> CRMOutboxEvent | None:
        """
        Dispatches subscription.updated event to CRM when package subscription status or plan changes.
        """
        tenant = package.tenant
        client_user = package.client
        contact_data = cls.serialize_contact(user=client_user)

        data_payload: dict[str, Any] = {
            "package_external_id": str(package.package_type_id),
            "package": package.package_type.name,
            "plan": package.package_type.name,
            "subscription_status": new_status or package.status,
            "amount": float(package.package_type.price or 0.0),
            "currency": (getattr(tenant, "currency", None) or "USD").upper(),
            "updated_at": timezone.now().isoformat(),
            "stripe_subscription_id": package.stripe_subscription_id or "",
            "package_id": str(package.id),
        }
        if reason:
            data_payload["reason"] = reason

        event_id = f"evt_subupd_{package.id}_{uuid.uuid4().hex[:8]}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type="subscription.updated",
            contact_data=contact_data,
            data=data_payload,
            event_id=event_id,
        )

    @classmethod
    def dispatch_subscription_expired_event(cls, package, reason: str = "validity_period_ended") -> CRMOutboxEvent | None:
        """
        Dispatches subscription.expired event to CRM when package passes expiration date.
        """
        tenant = package.tenant
        client_user = package.client
        contact_data = cls.serialize_contact(user=client_user)

        data_payload: dict[str, Any] = {
            "package_external_id": str(package.package_type_id),
            "package": package.package_type.name,
            "plan": package.package_type.name,
            "expiration_reason": reason,
            "reason": reason,
            "expired_at": (package.expires_at or timezone.now()).isoformat(),
            "stripe_subscription_id": package.stripe_subscription_id or "",
            "package_id": str(package.id),
        }

        event_id = f"evt_subexp_{package.id}_{uuid.uuid4().hex[:8]}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type="subscription.expired",
            contact_data=contact_data,
            data=data_payload,
            event_id=event_id,
        )

    @classmethod
    def dispatch_payment_succeeded_event(cls, payment) -> CRMOutboxEvent | None:
        """
        Dispatches payment.succeeded event to CRM upon any completed payment.
        """
        tenant = payment.tenant
        client_user = payment.client
        contact_data = cls.serialize_contact(user=client_user)

        data_payload: dict[str, Any] = {
            "amount": float(payment.amount) if payment.amount else 0.0,
            "currency": (getattr(tenant, "currency", None) or "USD").upper(),
            "paid_at": (payment.created_at or timezone.now()).isoformat(),
            "payment_id": str(payment.id),
            "payment_type": payment.type,
            "provider_ref": payment.provider_ref or "",
        }
        if getattr(payment, "package", None) and payment.package.package_type:
            data_payload["package_external_id"] = str(payment.package.package_type_id)
            data_payload["package"] = payment.package.package_type.name
            data_payload["plan"] = payment.package.package_type.name
            data_payload["package_id"] = str(payment.package_id)

        event_id = f"evt_paysucc_{payment.idempotency_key or payment.id}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type="payment.succeeded",
            contact_data=contact_data,
            data=data_payload,
            event_id=event_id,
        )

    @classmethod
    def dispatch_refund_issued_event(
        cls,
        *,
        tenant: Tenant,
        user,
        amount: float,
        currency: str,
        reason: str = "",
        refund_amount: float = None,
        package=None,
    ) -> CRMOutboxEvent | None:
        """
        Dispatches refund.issued event to CRM when a charge is refunded.
        """
        contact_data = cls.serialize_contact(user=user)
        ref_amt = refund_amount if refund_amount is not None else amount

        data_payload: dict[str, Any] = {
            "amount": amount,
            "refund_amount": ref_amt,
            "currency": currency.upper(),
            "reason": reason or "Customer refund",
            "refund_reason": reason or "Customer refund",
            "refunded_at": timezone.now().isoformat(),
        }
        if package and package.package_type:
            data_payload["package_external_id"] = str(package.package_type_id)
            data_payload["package"] = package.package_type.name
            data_payload["plan"] = package.package_type.name

        event_id = f"evt_refund_{uuid.uuid4().hex}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type="refund.issued",
            contact_data=contact_data,
            data=data_payload,
            event_id=event_id,
        )

    @classmethod
    def dispatch_subscription_renewed_event(cls, package, payment, period_end=None) -> CRMOutboxEvent | None:
        """
        Dispatches subscription.renewed event to CRM on recurring package renewal.
        """
        tenant = package.tenant
        client_user = package.client
        contact_data = cls.serialize_contact(user=client_user)

        data_payload: dict[str, Any] = {
            "package_external_id": str(package.package_type_id),
            "package": package.package_type.name,
            "plan": package.package_type.name,
            "amount": float(payment.amount) if payment and payment.amount else 0.0,
            "currency": (getattr(tenant, "currency", None) or "USD").upper(),
            "renewed_at": timezone.now().isoformat(),
            "payment_id": str(payment.id) if payment else "",
            "stripe_subscription_id": package.stripe_subscription_id or "",
        }
        if period_end:
            data_payload["period_end"] = period_end

        event_id = f"evt_renew_{payment.idempotency_key or payment.id}" if payment else f"evt_renew_{uuid.uuid4().hex}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type="subscription.renewed",
            contact_data=contact_data,
            data=data_payload,
            event_id=event_id,
        )

    @classmethod
    def dispatch_subscription_cancelled_event(cls, package, reason: str = "") -> CRMOutboxEvent | None:
        """
        Dispatches subscription.cancelled event to CRM when package subscription is cancelled.
        """
        tenant = package.tenant
        client_user = package.client
        contact_data = cls.serialize_contact(user=client_user)

        data_payload: dict[str, Any] = {
            "package_external_id": str(package.package_type_id),
            "package": package.package_type.name,
            "plan": package.package_type.name,
            "cancellation_reason": reason or "Client cancellation",
            "cancelled_at": timezone.now().isoformat(),
            "stripe_subscription_id": package.stripe_subscription_id or "",
        }

        event_id = f"evt_cancel_{package.id}_{uuid.uuid4().hex[:8]}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type="subscription.cancelled",
            contact_data=contact_data,
            data=data_payload,
            event_id=event_id,
        )

    @classmethod
    def dispatch_payment_failed_event(
        cls,
        *,
        tenant: Tenant,
        user,
        amount: float,
        currency: str,
        reason: str = "",
        package_type=None,
    ) -> CRMOutboxEvent | None:
        """
        Dispatches payment.failed event to CRM when an invoice payment fails.
        """
        contact_data = cls.serialize_contact(user=user)
        data_payload: dict[str, Any] = {
            "amount": amount,
            "currency": currency.upper(),
            "reason": reason or "Card payment failed",
            "failure_reason": reason or "Card payment failed",
        }
        if package_type:
            data_payload["package_external_id"] = str(package_type.id)
            data_payload["package"] = package_type.name

        event_id = f"evt_payfail_{uuid.uuid4().hex}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type="payment.failed",
            contact_data=contact_data,
            data=data_payload,
            event_id=event_id,
        )

    @classmethod
    def dispatch_appointment_event(cls, appointment, action: str) -> CRMOutboxEvent | None:
        """
        Dispatches appointment lifecycle event (booked, cancelled, rescheduled, completed).
        """
        action_map = {
            "booked": "appointment.booked",
            "cancelled": "appointment.cancelled",
            "rescheduled": "appointment.rescheduled",
            "completed": "appointment.completed",
        }
        event_type = action_map.get(action.lower())
        if not event_type:
            return None

        tenant = appointment.tenant
        contact_data = cls.serialize_contact(user=appointment.client)

        location_name = appointment.location.name if appointment.location else ""
        data_payload: dict[str, Any] = {
            "external_ref": str(appointment.id),
            "starts_at": appointment.start_at.isoformat() if appointment.start_at else "",
            "location": location_name,
            "status": appointment.status,
            "provider_email": appointment.provider.email if appointment.provider else "",
        }

        event_id = f"evt_appt_{appointment.id}_{action}_{uuid.uuid4().hex[:8]}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type=event_type,
            contact_data=contact_data,
            data=data_payload,
            event_id=event_id,
        )

    @classmethod
    def dispatch_customer_updated_event(cls, user) -> CRMOutboxEvent | None:
        """
        Dispatches customer.updated event when client profile information changes.
        """
        tenant = getattr(user, "tenant", None)
        if not tenant:
            return None

        contact_data = cls.serialize_contact(user=user)
        profile = getattr(user, "profile", None)
        fn = getattr(user, "first_name", "") or (getattr(profile, "first_name", "") if profile else "")
        ln = getattr(user, "last_name", "") or (getattr(profile, "last_name", "") if profile else "")
        phone = getattr(user, "phone_number", "") or (getattr(profile, "phone_number", "") if profile else "")

        data_payload: dict[str, Any] = {
            "first_name": fn,
            "last_name": ln,
            "email": user.email,
            "phone": phone,
        }
        if profile:
            if profile.city:
                data_payload["city"] = profile.city
            if profile.country:
                data_payload["country"] = profile.country

        event_id = f"evt_custupd_{user.id}_{uuid.uuid4().hex[:8]}"
        return cls.dispatch_event(
            tenant=tenant,
            event_type="customer.updated",
            contact_data=contact_data,
            data=data_payload,
            event_id=event_id,
        )
