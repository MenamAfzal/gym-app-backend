import os
import sys
import django

# Setup CRM Django environment
crm_dir = "/Users/ansmustafa/.gemini/antigravity/brain/9320c4b1-3294-4cc0-8262-833e571bea00/scratch/crm_backend"
sys.path.insert(0, crm_dir)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
os.environ["DATABASE_URL"] = f"sqlite:///{crm_dir}/test_crm_live.sqlite3"
os.environ["CACHE_BACKEND"] = "django.core.cache.backends.locmem.LocMemCache"
os.environ["REDIS_URL"] = ""

django.setup()

from core.models import Tenant
from accounts.models import User, Membership, TenantAPIKey
from contacts.models import Contact, LeadStatus
from automation.models import Workflow, WorkflowEnrollment, WorkflowStep
from core.context import set_current_tenant, clear_current_tenant

clear_current_tenant()

# 1. Tenant
tenant, _ = Tenant.objects.get_or_create(
    slug="alpha-gym-crm",
    defaults={"name": "Alpha Gym CRM", "is_active": True}
)
set_current_tenant(tenant)

# 2. Owner User
user, _ = User.objects.get_or_create(
    email="crm_owner@alphagym.com",
    defaults={"first_name": "CRM", "last_name": "Owner", "is_active": True}
)
if not user.has_usable_password():
    user.set_password("Password123!")
    user.save()

# 3. Owner Membership
membership, _ = Membership.objects.get_or_create(
    tenant=tenant,
    user=user,
    defaults={"role": Membership.Role.OWNER, "is_active": True}
)

# 4. Tenant API Key (known key_id and secret)
KEY_ID = "crm_fitverx_live_test_key"
RAW_SECRET = "secret_fitverx_live_test_1234567890abcdef"

api_key = TenantAPIKey.objects.filter(key_id=KEY_ID).first()
if not api_key:
    api_key = TenantAPIKey.objects.create(
        tenant=tenant,
        name="Fitverx Integration Live Key",
        key_id=KEY_ID,
        secret_encrypted=RAW_SECRET,
        scopes=["*"],
        is_active=True,
    )
else:
    api_key.secret_encrypted = RAW_SECRET
    api_key.is_active = True
    api_key.scopes = ["*"]
    api_key.save()

# 5. Lead Statuses
new_status, _ = LeadStatus.objects.get_or_create(name="New", defaults={"order": 1, "is_default": True})
customer_status, _ = LeadStatus.objects.get_or_create(name="Customer", defaults={"order": 6, "is_default": False})

# 6. Workflows
lead_wf, _ = Workflow.objects.get_or_create(
    name="Lead Follow-Up Sequence",
    defaults={"status": Workflow.Status.ACTIVE, "trigger_type": "event"}
)
if lead_wf.status != Workflow.Status.ACTIVE:
    lead_wf.status = Workflow.Status.ACTIVE
    lead_wf.save(update_fields=["status"])

onboarding_wf, _ = Workflow.objects.get_or_create(
    name="Post-Purchase Onboarding",
    defaults={"status": Workflow.Status.ACTIVE, "trigger_type": "event"}
)
if onboarding_wf.status != Workflow.Status.ACTIVE:
    onboarding_wf.status = Workflow.Status.ACTIVE
    onboarding_wf.save(update_fields=["status"])

# Add step to onboarding workflow so enrollment succeeds
step, _ = WorkflowStep.objects.get_or_create(
    tenant=tenant,
    workflow=onboarding_wf,
    key="step_1",
    defaults={
        "type": WorkflowStep.StepType.ACTION,
        "subtype": WorkflowStep.Subtype.ADD_TAG,
        "config": {"tag": "Onboarding Started"},
        "position": 1,
    }
)
onboarding_wf.entry_step_key = "step_1"
onboarding_wf.save(update_fields=["entry_step_key"])

# 7. Pre-existing Lead Contact with active enrollment in Lead Follow-Up Sequence
contact, _ = Contact.objects.get_or_create(
    email="layla.fitness@example.com",
    defaults={
        "first_name": "Layla",
        "last_name": "Vance",
        "phone": "+14155550199",
        "lead_status": new_status,
        "source_channel": "landing_page",
    }
)

# Active enrollment in lead follow up sequence
open_enrollment = WorkflowEnrollment.objects.filter(
    workflow=lead_wf,
    contact=contact,
    status__in=[WorkflowEnrollment.Status.ACTIVE, WorkflowEnrollment.Status.PAUSED]
).first()

if not open_enrollment:
    open_enrollment = WorkflowEnrollment.objects.create(
        tenant=tenant,
        workflow=lead_wf,
        contact=contact,
        status=WorkflowEnrollment.Status.ACTIVE,
    )
else:
    open_enrollment.status = WorkflowEnrollment.Status.ACTIVE
    open_enrollment.save(update_fields=["status"])

print("CRM Fixtures configured successfully:")
print(f"Tenant ID: {tenant.id}")
print(f"Key ID: {KEY_ID}")
print(f"Raw Secret: {RAW_SECRET}")
print(f"Lead Workflow: {lead_wf.name} (id: {lead_wf.id})")
print(f"Onboarding Workflow: {onboarding_wf.name} (id: {onboarding_wf.id})")
print(f"Contact ID: {contact.id} (enrolled in {lead_wf.name})")
