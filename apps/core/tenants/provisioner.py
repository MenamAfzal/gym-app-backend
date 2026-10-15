"""
Tenant Provisioning Service
===========================
Orchestrates automatic initialization and seeding of all required modules, defaults,
and data whenever a new Tenant is created or provisioned:
1. Reflection & Cycle Tracker (Symptom categories & tags)
2. Reflection Focus Options (System daily focus goals)
3. Workout Movement Library (Exercises, Equipment, Movement patterns)
4. Scheduling (Tenant Booking Settings)
5. Loyalty & Rewards (Default Reward Program)
6. Billing (Free Billing Plan Subscription)
"""
import logging
from apps.core.tenants.context import bypass_tenant_isolation

logger = logging.getLogger(__name__)


class TenantProvisioningService:

    @classmethod
    def provision_new_tenant(cls, tenant, stdout=None):
        """
        Provisions all default data, modules, and configurations for a Tenant.
        Safe, idempotent, and fault-tolerant.
        """
        msg = f"--- Initializing modules and default data for Tenant: {tenant.name} ({tenant.subdomain}) ---"
        if stdout:
            stdout.write(msg)
        else:
            logger.info(msg)

        results = {}

        with bypass_tenant_isolation():
            # 1. Seed Symptoms (Cycle Tracker & Reflection)
            try:
                from apps.reflection_logger.services.seeder import seed_symptoms_for_tenant
                seed_symptoms_for_tenant(tenant, stdout=stdout)
                results['symptoms'] = 'success'
            except Exception as e:
                logger.exception("Failed to seed symptoms for tenant '%s': %s", tenant.name, e)
                results['symptoms'] = f'error: {e}'

            # 2. Seed Focus Options (Morning & Evening Reflections)
            try:
                from apps.reflection_logger.services.seeder import seed_focus_for_tenant
                seed_focus_for_tenant(tenant, stdout=stdout)
                results['focus'] = 'success'
            except Exception as e:
                logger.exception("Failed to seed focus options for tenant '%s': %s", tenant.name, e)
                results['focus'] = f'error: {e}'

            # 3. Seed Workout Movement Library (Exercises, Equipment, Movement Patterns)
            try:
                from apps.workout.services.seeder import seed_workout_library_for_tenant
                workout_stats = seed_workout_library_for_tenant(tenant, stdout=stdout)
                results['workout_library'] = workout_stats
            except Exception as e:
                logger.exception("Failed to seed workout library for tenant '%s': %s", tenant.name, e)
                results['workout_library'] = f'error: {e}'

            # 4. Scheduling & Booking Settings
            try:
                from apps.scheduling.models import TenantBookingSettings
                TenantBookingSettings.get_or_create_for_tenant(tenant)
                if stdout:
                    stdout.write(f"Configured TenantBookingSettings for '{tenant.name}'.")
                else:
                    logger.info("Configured TenantBookingSettings for '%s'.", tenant.name)
                results['booking_settings'] = 'success'
            except Exception as e:
                logger.exception("Failed to configure booking settings for tenant '%s': %s", tenant.name, e)
                results['booking_settings'] = f'error: {e}'

            # 5. Loyalty & Rewards Program
            try:
                from apps.rewards.models import RewardProgram, ProgramType, ProgramStatus
                reward_prog, created = RewardProgram.objects.get_or_create(
                    tenant=tenant,
                    name="Default Loyalty Program",
                    defaults={
                        "program_type": ProgramType.LOYALTY,
                        "description": "Default loyalty points and rewards program",
                        "status": ProgramStatus.ACTIVE
                    }
                )
                status_str = "Created" if created else "Existing"
                if stdout:
                    stdout.write(f"{status_str} default RewardProgram for '{tenant.name}'.")
                else:
                    logger.info("%s default RewardProgram for '%s'.", status_str, tenant.name)
                results['reward_program'] = 'success'
            except Exception as e:
                logger.exception("Failed to create default reward program for tenant '%s': %s", tenant.name, e)
                results['reward_program'] = f'error: {e}'

            # 6. Assign Free Billing Plan
            try:
                from apps.payments.models import BillingPlan, TenantBillingSubscription
                try:
                    free_plan = BillingPlan.objects.get(slug=BillingPlan.PlanSlug.FREE)
                    if not TenantBillingSubscription.objects.filter(tenant=tenant).exists():
                        TenantBillingSubscription.objects.create(
                            tenant=tenant,
                            billing_plan=free_plan,
                            status=TenantBillingSubscription.StatusChoices.ACTIVE,
                        )
                        if stdout:
                            stdout.write(f"Assigned Free BillingPlan to '{tenant.name}'.")
                        else:
                            logger.info("Assigned Free BillingPlan to '%s'.", tenant.name)
                    results['billing_plan'] = 'success'
                except BillingPlan.DoesNotExist:
                    logger.warning("Tenant '%s' created but 'free' BillingPlan does not exist.", tenant.name)
                    results['billing_plan'] = 'free_plan_not_found'
            except Exception as e:
                logger.exception("Failed to assign free billing plan for tenant '%s': %s", tenant.name, e)
                results['billing_plan'] = f'error: {e}'

        completed_msg = f"Successfully completed provisioning for tenant: {tenant.name} ({tenant.subdomain})"
        if stdout:
            stdout.write(completed_msg)
        else:
            logger.info(completed_msg)

        return results
