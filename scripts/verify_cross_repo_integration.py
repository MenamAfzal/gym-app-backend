import os
import sys
import json
import time
import uuid
import requests
import subprocess

# 1. Setup Fitverx Django environment
fitverx_dir = "/Users/ansmustafa/gym-app-backend"
sys.path.insert(0, fitverx_dir)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")

import django
django.setup()

from apps.core.tenants.models import Tenant
from apps.crm.client import CRMClient
from apps.crm.models import TenantCRMConfig, CRMOutboxEvent
from apps.crm.services import CRMIntegrationService
from apps.crm.tasks import deliver_crm_event_task
from apps.scheduling.models import PackageType, Location, Package, Payment
from apps.payments.models import ExternalCheckoutSession
from apps.users.models import User, UserRole
from django.utils import timezone
from decimal import Decimal

CRM_URL = "http://127.0.0.1:8001"
API_KEY_ID = "crm_fitverx_live_test_key"
API_SECRET = "secret_fitverx_live_test_1234567890abcdef"


def run_crm_db(code_str: str) -> dict:
    crm_dir = "/Users/ansmustafa/.gemini/antigravity/brain/9320c4b1-3294-4cc0-8262-833e571bea00/scratch/crm_backend"
    cmd = [
        "/Users/ansmustafa/gym-app-backend/venv/bin/python",
        "manage.py",
        "shell",
        "--settings=config.settings.dev",
        "-c",
        f"""
import json, sys
{code_str}
"""
    ]
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///test_crm_live.sqlite3"
    env["CACHE_BACKEND"] = "django.core.cache.backends.locmem.LocMemCache"
    env["CELERY_BROKER_URL"] = "memory://"
    env["CELERY_RESULT_BACKEND"] = "cache+memory://"
    res = subprocess.run(cmd, cwd=crm_dir, env=env, capture_output=True, text=True)
    if res.returncode != 0:
        print("CRM DB Script Error:")
        print("STDOUT:", res.stdout)
        print("STDERR:", res.stderr)
        raise RuntimeError(f"CRM DB script failed with code {res.returncode}")
    lines = [l for l in res.stdout.strip().split("\n") if l.strip()]
    for line in reversed(lines):
        try:
            return json.loads(line)
        except Exception:
            continue
    return {}


def crm_process_event(event_uuid: str) -> dict:
    return run_crm_db(f"""
from core.context import tenant_context
from webhooks.models import ExternalEvent
from webhooks.services import process_external_event
raw_event = ExternalEvent.unscoped_objects.get(pk="{event_uuid}")
with tenant_context(raw_event.tenant_id):
    ext_event, should_retry = process_external_event(str(raw_event.id))
    print(json.dumps({{"status": ext_event.status, "error": ext_event.error or ""}}))
""")


def crm_reset_lead_for_purchase(email: str) -> dict:
    return run_crm_db(f"""
from contacts.models import Contact
from automation.models import Workflow, WorkflowEnrollment, WorkflowStep
c = Contact.unscoped_objects.get(email="{email}")
lead_wf = Workflow.unscoped_objects.get(name="Lead Follow-Up Sequence")
WorkflowEnrollment.unscoped_objects.filter(workflow=lead_wf, contact=c).delete()
WorkflowEnrollment.unscoped_objects.create(
    tenant_id=c.tenant_id,
    workflow=lead_wf,
    contact=c,
    status=WorkflowEnrollment.Status.ACTIVE,
    current_step_key="step_lead_1",
)

onboarding_wf = Workflow.unscoped_objects.filter(name="Post-Purchase Onboarding").first()
if onboarding_wf:
    WorkflowEnrollment.unscoped_objects.filter(workflow=onboarding_wf, contact=c).delete()
    WorkflowStep.unscoped_objects.get_or_create(
        tenant_id=c.tenant_id,
        workflow=onboarding_wf,
        key="step_1",
        defaults={{
            "type": WorkflowStep.StepType.ACTION,
            "subtype": WorkflowStep.Subtype.ADD_TAG,
            "config": {{"tag": "Onboarding Started"}},
            "position": 1,
        }}
    )
    onboarding_wf.entry_step_key = "step_1"
    onboarding_wf.save(update_fields=["entry_step_key"])

print(json.dumps({{"reset": True}}))
""")


def crm_get_contact(email: str) -> dict:
    return run_crm_db(f"""
from core.context import tenant_context
from contacts.models import Contact
try:
    c = Contact.unscoped_objects.get(email="{email}")
    with tenant_context(c.tenant_id):
        print(json.dumps({{
            "found": True,
            "lead_status": c.lead_status.name if c.lead_status else None,
            "tags": list(c.tags.values_list("name", flat=True)),
            "converted_at": c.converted_at.isoformat() if c.converted_at else None,
            "custom_fields": c.custom_fields,
            "city": c.city,
        }}))
except Contact.DoesNotExist:
    print(json.dumps({{"found": False}}))
""")


def crm_get_workflow_statuses(email: str) -> dict:
    return run_crm_db(f"""
from core.context import tenant_context
from contacts.models import Contact
from automation.models import Workflow, WorkflowEnrollment
c = Contact.unscoped_objects.get(email="{email}")
with tenant_context(c.tenant_id):
    lead_wf = Workflow.unscoped_objects.filter(name="Lead Follow-Up Sequence").first()
    old_enr = WorkflowEnrollment.unscoped_objects.filter(workflow=lead_wf, contact=c).first() if lead_wf else None
    onboarding_wf = Workflow.unscoped_objects.filter(name="Post-Purchase Onboarding").first()
    new_enr = WorkflowEnrollment.unscoped_objects.filter(workflow=onboarding_wf, contact=c).first() if onboarding_wf else None
    print(json.dumps({{
        "lead_enrollment_status": old_enr.status if old_enr else None,
        "onboarding_enrollment_status": new_enr.status if new_enr else None,
    }}))
""")


def crm_get_contact_count(email: str) -> int:
    res = run_crm_db(f"""
from contacts.models import Contact
count = Contact.unscoped_objects.filter(email="{email}").count()
print(json.dumps({{"count": count}}))
""")
    return res.get("count", 0)


def run_checks():
    print("=" * 70)
    print("STARTING CROSS-REPOSITORY INTEGRATION VERIFICATION")
    print("Fitverx Backend <---> CRM Backend (Live Server)")
    print("=" * 70)

    # Verify CRM server is reachable
    health_resp = requests.get(f"{CRM_URL}/health/", timeout=5)
    assert health_resp.status_code == 200, f"CRM health check failed: {health_resp.status_code}"
    print("[PASS] CRM live server is healthy:", health_resp.json())

    client = CRMClient(
        base_url=CRM_URL,
        api_key_id=API_KEY_ID,
        api_secret=API_SECRET,
        timeout=10,
    )

    # -------------------------------------------------------------
    # 1. HMAC Authentication & Replay Protection Verification
    # -------------------------------------------------------------
    print("\n--- 1. Testing HMAC Authentication & Canonicalization ---")

    # A. Valid signature
    headers = client._build_headers()
    assert "X-API-Key" in headers
    assert "X-Timestamp" in headers
    assert "X-Signature" in headers
    assert "X-Tenant" not in headers

    # B. Tampered signature
    tampered_headers = dict(headers)
    tampered_headers["X-Signature"] = "deadbeef" * 8
    tampered_resp = requests.post(f"{CRM_URL}/api/v1/external/packages/sync/", data=b'{"packages":[]}', headers=tampered_headers)
    assert tampered_resp.status_code in (401, 403), f"Expected 401/403 for tampered signature, got {tampered_resp.status_code}"
    assert "Invalid HMAC signature" in tampered_resp.text
    print(f"[PASS] Tampered HMAC signature correctly rejected with {tampered_resp.status_code} ({tampered_resp.json().get('error', {}).get('message')})")

    # C. Expired timestamp (>300s window)
    expired_ts = str(int(time.time()) - 360)
    canonical = f"{expired_ts}.".encode("utf-8") + b'{"packages":[]}'
    import hmac, hashlib
    sig = hmac.new(API_SECRET.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
    expired_headers = {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY_ID,
        "X-Timestamp": expired_ts,
        "X-Signature": sig,
    }
    expired_resp = requests.post(f"{CRM_URL}/api/v1/external/packages/sync/", data=b'{"packages":[]}', headers=expired_headers)
    assert expired_resp.status_code in (401, 403), f"Expected 401/403 for expired timestamp, got {expired_resp.status_code}"
    assert "outside the 5-minute tolerance window" in expired_resp.text
    print(f"[PASS] Expired timestamp (>300s) correctly rejected with {expired_resp.status_code} ({expired_resp.json().get('error', {}).get('message')})")

    # D. Forbidden X-Tenant header
    forbidden_headers = dict(headers)
    forbidden_headers["X-Tenant"] = "some-tenant-id"
    forbidden_resp = requests.post(f"{CRM_URL}/api/v1/external/packages/sync/", data=b'{"packages":[]}', headers=forbidden_headers)
    assert forbidden_resp.status_code == 403, f"Expected 403 for X-Tenant header, got {forbidden_resp.status_code}"
    assert "X-Tenant header is not permitted" in forbidden_resp.text
    print("[PASS] X-Tenant header rejected with 403 Forbidden (CRM enforces tenant from API key)")

    # -------------------------------------------------------------
    # 2. Package Synchronization Verification
    # -------------------------------------------------------------
    print("\n--- 2. Testing Package Catalog Synchronization ---")

    pkg_ext_id = f"pkg_fitverx_{uuid.uuid4().hex[:8]}"
    package_item = {
        "external_id": pkg_ext_id,
        "name": "Live Transformation Pass",
        "description": "30 classes valid for 90 days",
        "price": "299.00",
        "currency": "USD",
        "billing_interval": "monthly",
        "status": "active",
        "features": ["30 Class Credits", "Nutrition Plan"],
        "metadata": {"credits": 30, "days": 90},
    }

    # A. Bulk Sync
    status_code, sync_data = client.sync_bulk_packages([package_item])
    assert status_code == 200, f"Bulk sync failed ({status_code}): {sync_data}"
    assert sync_data.get("status") == "success"
    assert sync_data.get("total") == 1
    assert sync_data.get("created") == 1
    print(f"[PASS] Package bulk sync accepted (200 OK): created package {pkg_ext_id}")

    # B. Retrieve Package Detail
    status_code, pkg_detail = client.get_package(pkg_ext_id)
    assert status_code == 200, f"Get package failed ({status_code}): {pkg_detail}"
    assert pkg_detail["external_id"] == pkg_ext_id
    assert pkg_detail["name"] == "Live Transformation Pass"
    assert float(pkg_detail["price"]) == 299.00
    print(f"[PASS] Package detail verified via GET: {pkg_detail['name']} (${pkg_detail['price']})")

    # C. Single Package Upsert (Update)
    package_item["name"] = "Live Transformation Pass (Updated)"
    package_item["price"] = "349.00"
    status_code, update_data = client.sync_package(package_item)
    assert status_code in (200, 201), f"Package update failed ({status_code}): {update_data}"
    assert update_data["name"] == "Live Transformation Pass (Updated)"
    assert float(update_data["price"]) == 349.00
    print(f"[PASS] Package update accepted (200 OK): updated price to ${update_data['price']}")

    # -------------------------------------------------------------
    # 3. Business Event Ingestion: package.purchased & Conversion
    # -------------------------------------------------------------
    print("\n--- 3. Testing package.purchased Ingestion & Conversion ---")

    purchase_event_id = f"evt_purchase_{uuid.uuid4().hex}"
    purchase_payload = {
        "event_id": purchase_event_id,
        "type": "package.purchased",
        "occurred_at": timezone.now().isoformat(),
        "contact": {
            "external_id": "fitverx_usr_layla_001",
            "email": "layla.fitness@example.com",
            "phone": "+14155550199",
            "first_name": "Layla",
            "last_name": "Vance",
        },
        "data": {
            "package_external_id": pkg_ext_id,
            "package": "Live Transformation Pass (Updated)",
            "plan": "Monthly",
            "amount": 349.00,
            "currency": "USD",
            "purchased_at": timezone.now().isoformat(),
        },
        "automation": [
            {"action": "stop", "workflow": "Lead Follow-Up Sequence"},
            {"action": "trigger", "workflow": "Post-Purchase Onboarding"},
        ],
    }

    # Reset lead contact enrollment in CRM DB before test
    crm_reset_lead_for_purchase("layla.fitness@example.com")

    status_code, resp_data = client.ingest_event(purchase_payload)
    assert status_code == 202, f"Event ingestion failed ({status_code}): {resp_data}"
    assert resp_data["status"] == "accepted"
    assert resp_data["idempotent_duplicate"] is False
    assert resp_data["event_id"] == purchase_event_id
    ext_event_uuid = resp_data["external_event_id"]
    print(f"[PASS] Event accepted by CRM (202 Accepted): ID {ext_event_uuid}")

    # Process event in CRM DB
    process_res = crm_process_event(ext_event_uuid)
    assert process_res.get("status") == "processed", f"Event processing failed: {process_res.get('error')}"
    print(f"[PASS] Event processed by CRM worker successfully: status={process_res.get('status')}")

    # Verify CRM Contact conversion
    contact_info = crm_get_contact("layla.fitness@example.com")
    assert contact_info.get("found") is True
    tag_names = contact_info.get("tags", [])
    assert "Customer" in tag_names, f"Expected tag 'Customer' in {tag_names}"
    assert "Converted" in tag_names, f"Expected tag 'Converted' in {tag_names}"
    assert contact_info.get("lead_status") == "Customer", f"Expected lead status Customer, got {contact_info.get('lead_status')}"
    assert contact_info.get("converted_at") is not None, "Expected converted_at to be populated"
    assert contact_info.get("custom_fields", {}).get("amount") == 349.00
    assert contact_info.get("custom_fields", {}).get("currency") == "USD"
    print(f"[PASS] CRM Contact conversion verified: LeadStatus='{contact_info.get('lead_status')}', Tags={tag_names}, ConvertedAt={contact_info.get('converted_at')}")

    # Verify Workflow Directives
    wf_info = crm_get_workflow_statuses("layla.fitness@example.com")
    assert wf_info.get("lead_enrollment_status") == "stopped", f"Expected lead workflow enrollment to be stopped, got {wf_info.get('lead_enrollment_status')}"
    print(f"[PASS] Promotional workflow 'Lead Follow-Up Sequence' stopped for contact (status={wf_info.get('lead_enrollment_status')})")

    assert wf_info.get("onboarding_enrollment_status") == "active", f"Expected onboarding workflow active, got {wf_info.get('onboarding_enrollment_status')}"
    print(f"[PASS] Onboarding workflow 'Post-Purchase Onboarding' triggered for contact (status={wf_info.get('onboarding_enrollment_status')})")

    # -------------------------------------------------------------
    # 4. Idempotency / Deduplication Verification
    # -------------------------------------------------------------
    print("\n--- 4. Testing Idempotent Duplicate Delivery ---")

    status_code, dup_resp = client.ingest_event(purchase_payload)
    assert status_code == 200, f"Expected 200 OK for duplicate event, got {status_code}: {dup_resp}"
    assert dup_resp["status"] == "duplicate"
    assert dup_resp["idempotent_duplicate"] is True
    assert dup_resp["event_id"] == purchase_event_id
    assert dup_resp["event_status"] == "processed"
    print(f"[PASS] Duplicate event correctly returned 200 OK with idempotent_duplicate=True")

    # Verify no duplicate contacts
    matching_contacts_count = crm_get_contact_count("layla.fitness@example.com")
    assert matching_contacts_count == 1, f"Expected 1 contact, found {matching_contacts_count}"
    print("[PASS] Verified duplicate events did NOT duplicate contact records")

    # -------------------------------------------------------------
    # 5. Additional Event Types Verification
    # -------------------------------------------------------------
    print("\n--- 5. Testing Additional Event Types ---")

    # A. subscription.renewed
    renew_evt_id = f"evt_renew_{uuid.uuid4().hex}"
    renew_status, renew_resp = client.ingest_event({
        "event_id": renew_evt_id,
        "type": "subscription.renewed",
        "occurred_at": timezone.now().isoformat(),
        "contact": {"email": "layla.fitness@example.com"},
        "data": {"package": "Live Transformation Pass", "amount": 349.00, "currency": "USD"},
    })
    assert renew_status == 202
    crm_process_event(renew_resp["external_event_id"])
    c_renew = crm_get_contact("layla.fitness@example.com")
    assert c_renew.get("custom_fields", {}).get("subscription_status") == "active"
    print("[PASS] subscription.renewed event accepted and processed (status='active')")

    # B. subscription.cancelled
    cancel_evt_id = f"evt_cancel_{uuid.uuid4().hex}"
    cancel_status, cancel_resp = client.ingest_event({
        "event_id": cancel_evt_id,
        "type": "subscription.cancelled",
        "occurred_at": timezone.now().isoformat(),
        "contact": {"email": "layla.fitness@example.com"},
        "data": {"cancellation_reason": "Relocating to another city"},
    })
    assert cancel_status == 202
    crm_process_event(cancel_resp["external_event_id"])
    c_cancel = crm_get_contact("layla.fitness@example.com")
    assert c_cancel.get("custom_fields", {}).get("subscription_status") == "cancelled"
    assert "Subscription Cancelled" in c_cancel.get("tags", [])
    print("[PASS] subscription.cancelled event accepted and processed (tagged 'Subscription Cancelled')")

    # C. payment.failed
    fail_evt_id = f"evt_fail_{uuid.uuid4().hex}"
    fail_status, fail_resp = client.ingest_event({
        "event_id": fail_evt_id,
        "type": "payment.failed",
        "occurred_at": timezone.now().isoformat(),
        "contact": {"email": "layla.fitness@example.com"},
        "data": {"amount": 349.00, "currency": "USD", "reason": "Insufficient funds"},
    })
    assert fail_status == 202
    crm_process_event(fail_resp["external_event_id"])
    c_fail = crm_get_contact("layla.fitness@example.com")
    assert "Payment Failed" in c_fail.get("tags", [])
    print("[PASS] payment.failed event accepted and processed (tagged 'Payment Failed')")

    # D. customer.updated
    upd_evt_id = f"evt_upd_{uuid.uuid4().hex}"
    upd_status, upd_resp = client.ingest_event({
        "event_id": upd_evt_id,
        "type": "customer.updated",
        "occurred_at": timezone.now().isoformat(),
        "contact": {"email": "layla.fitness@example.com"},
        "data": {"city": "Austin", "country": "US"},
    })
    assert upd_status == 202
    crm_process_event(upd_resp["external_event_id"])
    c_upd = crm_get_contact("layla.fitness@example.com")
    assert c_upd.get("city") == "Austin"
    print(f"[PASS] customer.updated event accepted and processed (city='{c_upd.get('city')}')")

    # E. Unsupported event type returns 422
    unsupported_status, unsupported_resp = client.ingest_event({
        "event_id": f"evt_unsupp_{uuid.uuid4().hex}",
        "type": "unsupported.custom.event",
        "occurred_at": timezone.now().isoformat(),
        "contact": {"email": "layla.fitness@example.com"},
    })
    assert unsupported_status == 422, f"Expected 422 for unsupported type, got {unsupported_status}"
    print("[PASS] Unsupported event type correctly rejected with 422 Unprocessable Entity")

    # -------------------------------------------------------------
    # 6. Fitverx Outbox Delivery & Retry Logic Verification
    # -------------------------------------------------------------
    print("\n--- 6. Testing Fitverx Outbox Event Delivery ---")

    fitverx_tenant, _ = Tenant.objects.get_or_create(
        subdomain="live-crm-tenant",
        defaults={"name": "Live CRM Test Tenant", "currency": "usd"}
    )
    crm_cfg, _ = TenantCRMConfig.all_objects.get_or_create(
        tenant=fitverx_tenant,
        defaults={
            "api_key_id": API_KEY_ID,
            "api_secret": API_SECRET,
            "crm_base_url": CRM_URL,
            "is_active": True,
        }
    )
    crm_cfg.api_key_id = API_KEY_ID
    crm_cfg.api_secret = API_SECRET
    crm_cfg.crm_base_url = CRM_URL
    crm_cfg.is_active = True
    crm_cfg.save()

    outbox_evt = CRMIntegrationService.dispatch_event(
        tenant=fitverx_tenant,
        event_type="customer.updated",
        contact_data={"email": "layla.fitness@example.com"},
        data={"city": "Dallas"},
    )
    assert outbox_evt is not None
    assert outbox_evt.status == CRMOutboxEvent.Status.PENDING

    # Run delivery task directly (or verify already delivered via eager celery)
    deliver_result = deliver_crm_event_task(str(outbox_evt.id))
    assert deliver_result["status"] in ("delivered", "already_delivered"), f"Delivery task failed: {deliver_result}"

    outbox_evt.refresh_from_db()
    assert outbox_evt.status == CRMOutboxEvent.Status.DELIVERED
    assert outbox_evt.response_status_code == 202
    print(f"[PASS] Fitverx outbox event {outbox_evt.event_id} delivered (HTTP {outbox_evt.response_status_code})")

    print("\n" + "=" * 70)
    print("ALL CROSS-REPOSITORY INTEGRATION CHECKS PASSED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    run_checks()
