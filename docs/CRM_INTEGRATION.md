# Fitverx ↔ CRM Integration Specification & Operations Guide

## 1. Architecture & Design Principles

The Fitverx ↔ CRM integration provides seamless, real-time, tenant-isolated data synchronization and marketing automation between the Fitverx fitness management backend and the external CRM platform.

```
+-----------------------------------------------------------------------------------------+
|                                    FITVERX BACKEND                                      |
|                                                                                         |
|   [Checkout / Stripe]       [Appointments]       [Package Catalog]      [User Profile]  |
|            │                       │                     │                     │        |
|            ▼                       ▼                     ▼                     ▼        |
|  StripePackageService      Appointment Signal    PackageType Signal   UserProfile Signal|
|            │                       │                     │                     │        |
|            └───────────────────────┼─────────────────────┴─────────────────────┘        |
|                                    ▼                                                    |
|                        CRMIntegrationService                                            |
|                                    │                                                    |
|                        +───────────────────────+                                        |
|                        │  transaction.atomic() │                                        |
|                        │  CRMOutboxEvent.save()│                                        |
|                        +───────────────────────+                                        |
|                                    │ transaction.on_commit                              |
|                                    ▼                                                    |
|                       Celery: deliver_crm_event_task                                    |
|                                    │                                                    |
|                         HMAC-SHA256 Signing                                             |
|                                    │                                                    |
+────────────────────────────────────┼────────────────────────────────────────────────────+
                                     │ HTTPS + HMAC Headers
                                     ▼
+-----------------------------------------------------------------------------------------+
|                                      CRM BACKEND                                        |
|                                                                                         |
|               POST /api/v1/external/packages/ (Catalog Sync)                            |
|               POST /api/v1/external/events/   (Event Ingestion)                         |
|                                    │                                                    |
|                                    ▼                                                    |
|                 ExternalHMACAuthentication (Tenant Scoped)                              |
|                                    │                                                    |
|                         process_external_event                                          |
|                                    │                                                    |
|             ┌──────────────────────┼──────────────────────┐                             |
|             ▼                      ▼                      ▼                             |
|   Contact Attribution     Purchase Creation     Automation Directives                   |
|   (Lead -> Customer)      (Package / Payment)   (Stop Leads / Trigger Onboarding)       |
+-----------------------------------------------------------------------------------------+
```

### Key Principles

1. **System of Record**: Fitverx is the strict authoritative source of truth for packages, purchases, payments, subscriptions, and appointments. The CRM is the destination for lead management, conversion tracking, customer communications, and marketing workflows.
2. **Tenant Isolation**: Every sync request and webhook event is tenant-scoped. Authentication uses CRM API Key IDs and Secrets mapped 1:1 per tenant (`TenantCRMConfig`). In accordance with CRM security contracts, the `X-Tenant` header is **omitted** when using API key authentication to prevent tenant spoofing.
3. **Transactional Outbox Delivery**: All external CRM events are persisted to the `CRMOutboxEvent` table inside the enclosing database transaction of the business operation. Celery dispatch tasks are queued exclusively via `transaction.on_commit(...)`, ensuring that aborted or rolled-back transactions never leak spurious events.
4. **Idempotency**: All purchase and payment events carry deterministic event IDs (`evt_purchase_<session_id>`, `evt_paysucc_<idempotency_key>`). The CRM responds with HTTP 200 and `idempotent_duplicate=True` on redelivery, preventing duplicate purchases, contacts, or workflow triggers.
5. **Zero CRM Modifications**: Fitverx adheres strictly to the existing CRM backend endpoints, serializers, models, and authentication contracts without requiring any modifications to the CRM codebase.

---

## 2. Authentication, Signatures & Security

### HMAC-SHA256 Canonicalization Contract

All requests to the CRM external API (`/api/v1/external/*`) require three authentication headers:

| Header | Format / Value | Description |
|---|---|---|
| `X-API-Key` | String | Public API key identifier registered in CRM (`TenantAPIKey.key_id`). |
| `X-Timestamp` | Unix epoch integer (seconds) | Request timestamp. Rejected by CRM if `abs(now - timestamp) > 300` seconds. |
| `X-Signature` | Hex-encoded HMAC-SHA256 | Signature calculated over canonical payload. |

#### Canonical String Construction
```python
canonical_string = f"{timestamp}.".encode("utf-8") + raw_request_body_bytes
signature = hmac.new(api_secret_bytes, canonical_string, hashlib.sha256).hexdigest()
```

### Credential Encryption at Rest

CRM API secrets are encrypted at rest in the Fitverx database using Fernet symmetric encryption:
- **Key Derivation**: Fernet 32-byte key is derived from `settings.SECRET_KEY` via SHA-256 HKDF (`apps.crm.models.encrypt_crm_secret`).
- **Ciphertext Storage**: `TenantCRMConfig.api_secret` is stored with an `enc::` prefix.
- **Transparent Decryption**: `config.get_api_secret()` and `config.raw_api_secret` decrypt the value in memory only when instantiating the HTTP client.

---

## 3. Complete Verified 14-Event Coverage Matrix

Fitverx supports 100% of the external event types accepted by the CRM backend:

| # | Event Type | Fitverx Dispatch Point | Trigger Condition | CRM Processing Behavior |
|---|---|---|---|---|
| 1 | `package.purchased` | `apps.payments.checkout_service.fulfill_checkout_webhook` | Customer completes package checkout session. | Converts lead to Customer, tags contact, creates Purchase record, triggers automation directives (`stop` lead sequence, `trigger` onboarding). |
| 2 | `subscription.created` | `apps.payments.checkout_service.fulfill_checkout_webhook`<br>`apps.payments.stripe_package_service.handle_checkout_session_completed` | Recurring subscription package initiated. | Records active subscription metadata against Contact, stores `stripe_subscription_id`. |
| 3 | `subscription.updated` | `apps.payments.stripe_package_service.handle_subscription_updated` | Stripe subscription updated (status, plan, dates). | Updates subscription status and renewal date in CRM contact metadata. |
| 4 | `subscription.renewed` | `apps.payments.stripe_package_service.handle_invoice_payment_succeeded` | Stripe subscription invoice paid on renewal. | Updates `subscription_status='active'`, extends validity, logs renewal amount. |
| 5 | `subscription.cancelled` | `apps.payments.stripe_package_service.handle_subscription_deleted` | Client or admin cancels recurring subscription. | Sets `subscription_status='cancelled'`, tags Contact with `Subscription Cancelled`. |
| 6 | `subscription.expired` | `apps.crm.tasks.reconcile_expired_packages_task` | Package validity period elapses without renewal. | Sets `subscription_status='expired'`, updates CRM contact status. |
| 7 | `payment.succeeded` | `apps.payments.checkout_service.fulfill_checkout_webhook`<br>`apps.payments.stripe_package_service.handle_invoice_payment_succeeded` | Any payment completes successfully. | Logs revenue attributed to contact, updates lifetime customer value. |
| 8 | `payment.failed` | `apps.payments.stripe_package_service.handle_invoice_payment_failed` | Stripe subscription invoice payment fails. | Tags Contact with `Payment Failed`, initiates CRM recovery sequence. |
| 9 | `refund.issued` | `apps.payments.stripe_package_service.handle_charge_refunded` | Charge refunded in Stripe. | Logs refund transaction, adjusts contact net spend. |
| 10 | `appointment.booked` | `apps.crm.signals.crm_appointment_saved` | Appointment created in Fitverx scheduling. | Tracks booking count, updates contact last activity. |
| 11 | `appointment.cancelled` | `apps.crm.signals.crm_appointment_saved` | Appointment status changes to `cancelled`. | Updates appointment record in CRM activity log. |
| 12 | `appointment.rescheduled` | `apps.crm.signals.crm_appointment_saved` | Appointment status changes to `rescheduled`. | Updates appointment timestamp in CRM activity timeline. |
| 13 | `appointment.completed` | `apps.crm.signals.crm_appointment_saved` | Appointment status changes to `completed`. | Increments completed session count for retention scoring. |
| 14 | `customer.updated` | `apps.crm.signals.crm_user_profile_saved` | Client updates profile (name, phone, address). | Synchronizes contact demographics in CRM (first/last name, phone, city, country). |

---

## 4. Automation Directives

When `package.purchased` is dispatched, Fitverx automatically embeds workflow automation directives configured on `TenantCRMConfig`:

```json
{
  "event_id": "evt_purchase_c9b88496-e6bf-4638-b118-2ad3fb38f877",
  "type": "package.purchased",
  "occurred_at": "2026-11-21T17:00:00Z",
  "contact": {
    "external_id": "fitverx_user_uuid",
    "email": "client@example.com",
    "first_name": "Sara",
    "last_name": "Malik",
    "full_name": "Sara Malik"
  },
  "data": {
    "package_external_id": "pkg_uuid",
    "amount": 299.00,
    "currency": "USD",
    "stripe_subscription_id": "sub_123456"
  },
  "automation": [
    {
      "action": "stop",
      "workflow": "Lead Follow-Up Sequence"
    },
    {
      "action": "trigger",
      "workflow": "Post-Purchase Onboarding"
    }
  ]
}
```

- **`action: "stop"`**: Instructs CRM `EnrollmentService` to stop active or paused promotional workflows for this contact, preventing prospective sales emails after purchase.
- **`action: "trigger"`**: Enrolls the contact in the onboarding sequence starting at step 1.

---

## 5. Resilient Delivery, Retries & Dead-Letter Handling

- **Celery Task**: `crm.deliver_crm_event_task(event_id)`
- **HTTP Status Handling**:
  - `202 Accepted` / `200 OK`: Marked as `DELIVERED`, response code stored.
  - `4xx Client Error` (e.g. `422 Unprocessable` or `403 Forbidden`): Marked as `FAILED`. No automatic retries to avoid flooding the CRM with malformed payloads.
  - `5xx Server Error` / Network Timeout: Retried with exponential backoff:
    - Retries: 5 attempts
    - Countdown schedule: `[30s, 60s, 180s, 600s, 1800s]`
    - Upon reaching attempt 5: Marked as `ABANDONED` (dead-letter queue).
- **Periodic Maintenance Sweep**: `crm.retry_pending_crm_events_task` runs hourly via Celery Beat to recover any stranded events.

---

## 6. Package Catalog Synchronization & Reconciliation

1. **Automatic Real-Time Sync**: On saving any `PackageType`:
   - Active package: Synced as `status='active'` with features, pricing, credits, and metadata.
   - Deactivated package (`is_active=False`): Synced as `status='archived'`.
2. **Periodic Catalog Reconciliation**: `crm.reconcile_all_tenant_packages_to_crm_task` sweeps all active CRM tenants daily to reconcile any drift via bulk sync (`POST /api/v1/external/packages/sync/`).
3. **Periodic Expiration Reconciliation**: `crm.reconcile_expired_packages_task` runs periodically to detect elapsed packages, mark them `status='expired'`, and dispatch `subscription.expired`.

---

## 7. Configuration & Tenant Setup Instructions

### Step 1: Provision API Credentials in CRM
In the CRM backend (or CRM Admin console):
1. Navigate to the tenant's workspace.
2. Create a new External API Key for Fitverx.
3. Note the generated **Key ID** (e.g., `crm_key_...`) and **Secret** (e.g., `crm_sec_...`).

### Step 2: Configure TenantCRMConfig in Fitverx
In the Fitverx Admin (`/admin/crm/tenantcrmconfig/`) or Django shell:
```python
from apps.core.tenants.models import Tenant
from apps.crm.models import TenantCRMConfig

tenant = Tenant.objects.get(subdomain="my-gym")
config, _ = TenantCRMConfig.objects.update_or_create(
    tenant=tenant,
    defaults={
        "api_key_id": "crm_key_live_abc123",
        "api_secret": "crm_sec_live_xyz789", # Auto-encrypted at rest
        "crm_base_url": "https://crm.fitverx.com",
        "is_active": True,
        "sync_packages_on_save": True,
        "stop_lead_workflow_name": "Lead Follow-Up Sequence",
        "start_onboarding_workflow_name": "Post-Purchase Onboarding",
    }
)
```

### Step 3: Initial Catalog Sync
Trigger the initial catalog synchronization:
```bash
python manage.py shell -c "
from apps.core.tenants.models import Tenant
from apps.crm.services import CRMIntegrationService
tenant = Tenant.objects.get(subdomain='my-gym')
success, resp = CRMIntegrationService.sync_all_packages_to_crm(tenant)
print('Catalog sync result:', success, resp)
"
```

---

## 8. Verification & Test Execution

Run the full Fitverx CRM test suites:

```bash
# CRM Contract & Dispatcher Test Suite (15 tests)
python manage.py test apps.crm.tests --settings=config.settings.test

# Payment Renewal Integration Tests (6 tests)
python manage.py test apps.payments.test_package_renewal --settings=config.settings.test

# End-to-End Cross-Repository Verification Against Running CRM Server
python scripts/verify_cross_repo_integration.py
```
