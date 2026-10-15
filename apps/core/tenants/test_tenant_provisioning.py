"""
Tests for Automatic Tenant Provisioning & Management Commands
"""
from io import StringIO
from django.test import TestCase
from django.core.management import call_command
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant, reset_current_tenant, bypass_tenant_isolation
from apps.reflection_logger.models import SymptomCategory, SymptomTag, FocusOption
from apps.workout.models import Exercise, Equipment, WorkoutTag
from apps.scheduling.models import TenantBookingSettings
from apps.rewards.models import RewardProgram, ProgramType, ProgramStatus


class TenantProvisioningTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Ironclad Athletic Club",
            subdomain="ironclad-test"
        )
        self.tenant_token = set_current_tenant(self.tenant)

    def tearDown(self):
        reset_current_tenant(self.tenant_token)

    def test_auto_provisioning_on_tenant_creation(self):
        """
        When a new Tenant is created, all data for Reflection, Cycle Tracker,
        Workout Movement Library, Booking Settings, and Reward Program must be automatically seeded.
        """
        with bypass_tenant_isolation():
            # 1. Reflection & Cycle Tracker Symptoms
            symptom_cats = SymptomCategory.objects.filter(tenant=self.tenant).count()
            self.assertEqual(symptom_cats, 7)

            symptom_tags = SymptomTag.objects.filter(tenant=self.tenant).count()
            self.assertGreaterEqual(symptom_tags, 60)

            # 2. Reflection Focus Options
            focus_opts = FocusOption.objects.filter(tenant=self.tenant).count()
            self.assertEqual(focus_opts, 21)

            # 3. Workout Movement Library
            exercises_count = Exercise.objects.filter(tenant=self.tenant).count()
            self.assertGreaterEqual(exercises_count, 1200)

            equipments_count = Equipment.objects.filter(tenant=self.tenant).count()
            self.assertEqual(equipments_count, 38)

            workout_tags_count = WorkoutTag.objects.filter(tenant=self.tenant).count()
            self.assertGreaterEqual(workout_tags_count, 18)

            # 4. Scheduling Booking Settings
            self.assertTrue(TenantBookingSettings.objects.filter(tenant=self.tenant).exists())

            # 5. Reward Program
            reward_program = RewardProgram.objects.filter(tenant=self.tenant).first()
            self.assertIsNotNone(reward_program)
            self.assertEqual(reward_program.program_type, ProgramType.LOYALTY)
            self.assertEqual(reward_program.status, ProgramStatus.ACTIVE)

    def test_seed_tenant_data_management_command(self):
        """
        Test that running `seed_tenant_data --tenant=<subdomain>` runs idempotently without errors or duplicating data.
        """
        out = StringIO()
        call_command("seed_tenant_data", tenant=self.tenant.subdomain, stdout=out)
        output = out.getvalue()
        self.assertIn("Successfully completed provisioning for tenant", output)

        with bypass_tenant_isolation():
            # Counts should remain consistent (idempotent)
            self.assertEqual(SymptomCategory.objects.filter(tenant=self.tenant).count(), 7)
            self.assertEqual(FocusOption.objects.filter(tenant=self.tenant).count(), 21)
            self.assertEqual(Equipment.objects.filter(tenant=self.tenant).count(), 38)

    def test_import_exercises_management_command(self):
        """
        Test that `import_exercises` works without mandatory positional args and targets the tenant.
        """
        out = StringIO()
        call_command("import_exercises", tenant=self.tenant.subdomain, stdout=out)
        output = out.getvalue()
        self.assertIn("Successfully finished", output)

    def test_seed_symptoms_and_focus_management_commands(self):
        """
        Test that `seed_symptoms` and `seed_focus` support `--tenant` parameter.
        """
        out_sym = StringIO()
        call_command("seed_symptoms", tenant=self.tenant.subdomain, stdout=out_sym)
        self.assertIn("Seeding completed successfully", out_sym.getvalue())

        out_foc = StringIO()
        call_command("seed_focus", tenant=self.tenant.subdomain, stdout=out_foc)
        self.assertIn("Focus options seeded successfully", out_foc.getvalue())
