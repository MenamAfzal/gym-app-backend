import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.prod')
django.setup()

from apps.users.models import User, UserRole
from apps.scheduling.models import ClassSession, Location, StaffLocation
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant

print("=== START DIAGNOSTICS ===")

# 1. Find User
user = User.objects.filter(email__icontains="teststaff").first()
if not user:
    user = User.objects.filter(role=UserRole.TRAINER).first()

if not user:
    print("ERROR: No trainer user found in database.")
    exit(1)

print(f"User: {user.email} (ID: {user.id}, Role: {user.role}, Tenant: {user.tenant})")

# Set active tenant context
if user.tenant:
    set_current_tenant(user.tenant)
else:
    print("ERROR: User has no tenant associated.")
    exit(1)

# 2. Find Location
location = Location.objects.all().first()
print(f"Location in DB: {location.name if location else 'NONE'} (ID: {getattr(location, 'id', None)})")

# 3. Check StaffLocation mapping
mappings = StaffLocation.objects.filter(staff=user)
print(f"StaffLocation mappings for this user count: {mappings.count()}")
for m in mappings:
    print(f"  - Mapped to Location: {m.location.name} (ID: {m.location.id})")

# 4. Check ClassSessions
sessions = ClassSession.objects.all()
print(f"Total ClassSessions for this tenant count: {sessions.count()}")
for s in sessions:
    print(f"  - Session: {s.template.name} | Trainer: {s.staff.email} | Location: {s.template.location.name} | Status: {s.status}")

# 5. Simulate ViewSet get_queryset
qs = ClassSession.objects.select_related('template', 'room', 'staff')
if getattr(user, 'role', None) in [UserRole.GYM_MANAGER, UserRole.TRAINER, UserRole.FRONT_DESK]:
    qs = qs.filter(template__location__stafflocation__staff=user).distinct()

print(f"Simulated get_queryset count: {qs.count()}")
for s in qs:
    print(f"  - Visible Session: {s.template.name}")

print("=== END DIAGNOSTICS ===")
