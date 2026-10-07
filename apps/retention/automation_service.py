import logging
from datetime import timedelta
from typing import List, Optional

from django.db import transaction
from django.utils import timezone

from apps.core.tenants.context import bypass_tenant_isolation
from apps.core.tenants.models import Tenant
from apps.notifications.models import (
    DeliveryPolicy, DeliveryRecord, NotificationInbox,
    NotificationPriority, NotificationSource, NotificationType
)
from apps.notifications.providers import NotificationDispatcher
from apps.scheduling.models import Package, Payment
from apps.users.models import User, UserRole

from .models import (
    ClientRetentionMetrics,
    RetentionActionType,
    RetentionCampaignActionLog,
    RetentionCampaignTrigger,
    RetentionChannel,
    RetentionTriggerType,
)
from .ai_service import RetentionAIService
from .services import SegmentQueryService

logger = logging.getLogger(__name__)


class RetentionAutomationService:
    """
    Automated retention intervention trigger engine.
    Scans client behavioral milestones (inactivity, billing failures, expiring passes),
    synthesizes tailored win-back messaging (via AI or rule templates), and dispatches
    multi-channel interventions while maintaining audit logs and attribution tracking.
    """

    @classmethod
    def evaluate_inactivity_triggers(cls, tenant=None) -> List[RetentionCampaignActionLog]:
        """
        Identifies clients crossing exactly trigger_value days of inactivity
        (days_since_last_visit == trigger_value) and fires actions if they
        haven't received the same trigger within the last 30 days.
        """
        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)
        logs_created = []

        with bypass_tenant_isolation():
            triggers_qs = RetentionCampaignTrigger.all_objects.filter(
                trigger_type=RetentionTriggerType.INACTIVITY,
                is_active=True
            ).select_related('tenant', 'target_segment')

            if tenant:
                triggers_qs = triggers_qs.filter(tenant=tenant)

            for trigger in triggers_qs:
                target_days = trigger.trigger_value
                # Query clients with matching days since last visit
                metrics_qs = ClientRetentionMetrics.all_objects.filter(
                    tenant=trigger.tenant,
                    days_since_last_visit=target_days,
                    client__is_active=True
                ).select_related('client', 'tenant')

                if trigger.target_segment and trigger.target_segment.filter_criteria:
                    metrics_qs = SegmentQueryService.apply_criteria(
                        metrics_qs,
                        trigger.target_segment.filter_criteria
                    )

                for metric in metrics_qs:
                    client = metric.client
                    # Check throttle: no identical trigger within 30 days
                    already_sent = RetentionCampaignActionLog.all_objects.filter(
                        tenant=trigger.tenant,
                        client=client,
                        trigger=trigger,
                        sent_at__gte=thirty_days_ago
                    ).exists()

                    if not already_sent:
                        log = cls.execute_trigger(client=client, trigger=trigger, metrics=metric)
                        if log:
                            logs_created.append(log)

        return logs_created

    @classmethod
    def evaluate_failed_payment_triggers(cls, tenant=None) -> List[RetentionCampaignActionLog]:
        """
        Evaluates clients with recent failed billing attempts without an automated follow-up.
        Throttled to once every 7 days per client per trigger.
        """
        now = timezone.now()
        seven_days_ago = now - timedelta(days=7)
        logs_created = []

        with bypass_tenant_isolation():
            triggers_qs = RetentionCampaignTrigger.all_objects.filter(
                trigger_type=RetentionTriggerType.FAILED_PAYMENT,
                is_active=True
            ).select_related('tenant', 'target_segment')

            if tenant:
                triggers_qs = triggers_qs.filter(tenant=tenant)

            for trigger in triggers_qs:
                # Find clients who experienced a failed payment in the last 7 days
                failed_client_ids = list(
                    Payment.all_objects.filter(
                        tenant=trigger.tenant,
                        status='failed',
                        created_at__gte=seven_days_ago
                    ).values_list('client_id', flat=True).distinct()
                )

                # Also include clients whose metrics report failed payments in last 90d
                metrics_qs = ClientRetentionMetrics.all_objects.filter(
                    tenant=trigger.tenant,
                    failed_payments_last_90d__gt=0,
                    client__is_active=True
                ).select_related('client', 'tenant')

                metric_map = {m.client_id: m for m in metrics_qs}
                candidate_ids = set(failed_client_ids) | set(metric_map.keys())

                for client_id in candidate_ids:
                    metric = metric_map.get(client_id)
                    client = metric.client if metric else User.objects.filter(id=client_id).first()
                    if not client:
                        continue

                    already_sent = RetentionCampaignActionLog.all_objects.filter(
                        tenant=trigger.tenant,
                        client_id=client_id,
                        trigger=trigger,
                        sent_at__gte=seven_days_ago
                    ).exists()

                    if not already_sent:
                        log = cls.execute_trigger(client=client, trigger=trigger, metrics=metric)
                        if log:
                            logs_created.append(log)

        return logs_created

    @classmethod
    def evaluate_expiry_triggers(cls, tenant=None) -> List[RetentionCampaignActionLog]:
        """
        Identifies active packages expiring exactly in trigger_value days and dispatches
        renewal reminders (throttled to once per 7 days).
        """
        now = timezone.now()
        seven_days_ago = now - timedelta(days=7)
        logs_created = []

        with bypass_tenant_isolation():
            triggers_qs = RetentionCampaignTrigger.all_objects.filter(
                trigger_type=RetentionTriggerType.PACKAGE_EXPIRY,
                is_active=True
            ).select_related('tenant', 'target_segment')

            if tenant:
                triggers_qs = triggers_qs.filter(tenant=tenant)

            for trigger in triggers_qs:
                target_days = max(0, trigger.trigger_value)
                target_date = (now + timedelta(days=target_days)).date()

                expiring_packages = Package.all_objects.filter(
                    tenant=trigger.tenant,
                    status='active',
                    credits_remaining__gt=0,
                    expires_at__date=target_date,
                    client__is_active=True
                ).select_related('client', 'tenant')

                processed_clients = set()
                for pkg in expiring_packages:
                    client = pkg.client
                    if client.id in processed_clients:
                        continue
                    processed_clients.add(client.id)

                    already_sent = RetentionCampaignActionLog.all_objects.filter(
                        tenant=trigger.tenant,
                        client=client,
                        trigger=trigger,
                        sent_at__gte=seven_days_ago
                    ).exists()

                    if not already_sent:
                        metric = ClientRetentionMetrics.all_objects.filter(
                            tenant=trigger.tenant,
                            client=client
                        ).first()
                        log = cls.execute_trigger(client=client, trigger=trigger, metrics=metric)
                        if log:
                            logs_created.append(log)

        return logs_created

    @classmethod
    def evaluate_custom_segment_triggers(cls, tenant=None) -> List[RetentionCampaignActionLog]:
        """
        Evaluates triggers tied to SavedSegment dynamic criteria (throttled to once per 30 days).
        """
        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)
        logs_created = []

        with bypass_tenant_isolation():
            triggers_qs = RetentionCampaignTrigger.all_objects.filter(
                trigger_type=RetentionTriggerType.CUSTOM_SEGMENT,
                is_active=True,
                target_segment__isnull=False
            ).select_related('tenant', 'target_segment')

            if tenant:
                triggers_qs = triggers_qs.filter(tenant=tenant)

            for trigger in triggers_qs:
                segment = trigger.target_segment
                base_qs = ClientRetentionMetrics.all_objects.filter(
                    tenant=trigger.tenant,
                    client__is_active=True
                ).select_related('client', 'tenant')

                filtered_qs = SegmentQueryService.apply_criteria(base_qs, segment.filter_criteria)

                for metric in filtered_qs:
                    client = metric.client
                    already_sent = RetentionCampaignActionLog.all_objects.filter(
                        tenant=trigger.tenant,
                        client=client,
                        trigger=trigger,
                        sent_at__gte=thirty_days_ago
                    ).exists()

                    if not already_sent:
                        log = cls.execute_trigger(client=client, trigger=trigger, metrics=metric)
                        if log:
                            logs_created.append(log)

        return logs_created

    @classmethod
    def execute_trigger(
        cls,
        client: User,
        trigger: RetentionCampaignTrigger,
        metrics: Optional[ClientRetentionMetrics] = None
    ) -> Optional[RetentionCampaignActionLog]:
        """
        Executes a retention trigger for a client:
        1. Synthesizes personalized copy (via AI) or formats template copy.
        2. Dispatches notification via NotificationDispatcher & creates DeliveryRecord.
        3. Records RetentionCampaignActionLog(action_type='sent').
        """
        if metrics is None:
            metrics = ClientRetentionMetrics.all_objects.filter(
                tenant=trigger.tenant,
                client=client
            ).first()

        first_name = client.first_name or client.full_name or "there"
        full_name = client.full_name or client.first_name or "Member"
        studio_name = trigger.tenant.name if trigger.tenant else "Our Studio"

        # Format Subject
        subject_template = trigger.template_subject or f"Important message from {studio_name}"
        subject = subject_template.replace("{{first_name}}", first_name).replace("{first_name}", first_name)
        subject = subject.replace("{{name}}", full_name).replace("{name}", full_name)
        subject = subject.replace("{{studio_name}}", studio_name).replace("{studio_name}", studio_name)

        # Message body determination
        ai_generated_body = ""
        if trigger.use_ai_personalization:
            body = RetentionAIService.generate_personalized_winback_copy(
                metrics=metrics,
                client=client,
                trigger=trigger
            )
            ai_generated_body = body
        else:
            body = trigger.template_body or "We would love to see you back at the studio soon!"
            body = body.replace("{{first_name}}", first_name).replace("{first_name}", first_name)
            body = body.replace("{{name}}", full_name).replace("{name}", full_name)
            body = body.replace("{{studio_name}}", studio_name).replace("{studio_name}", studio_name)

        # Dispatch notification and persist DeliveryRecord
        try:
            inbox_item = NotificationInbox.objects.create(
                tenant=trigger.tenant,
                recipient=client,
                title=subject,
                body=body,
                notification_type=NotificationType.GENERAL,
                priority=NotificationPriority.NORMAL,
                source=NotificationSource.CAMPAIGN,
                delivery_policy=(
                    DeliveryPolicy.PUSH_AND_EMAIL
                    if trigger.channel in [RetentionChannel.EMAIL, RetentionChannel.PUSH]
                    else DeliveryPolicy.PUSH_ONLY
                ),
                action_payload={
                    "trigger_id": str(trigger.id),
                    "trigger_type": trigger.trigger_type,
                    "channel": trigger.channel,
                }
            )

            NotificationDispatcher.dispatch(inbox_item)

            # Ensure delivery record is recorded for tracking
            if not inbox_item.delivery_records.exists():
                DeliveryRecord.objects.create(
                    inbox_item=inbox_item,
                    channel='email' if trigger.channel == RetentionChannel.EMAIL else 'push',
                    email_address=client.email if trigger.channel == RetentionChannel.EMAIL else '',
                    status='sent' if inbox_item.email_sent or inbox_item.push_sent else 'sent',
                    attempted_at=timezone.now(),
                )
        except Exception as err:
            logger.warning(f"RetentionAutomationService: notification dispatch failed: {err}")

        # Create RetentionCampaignActionLog
        now = timezone.now()
        action_log = RetentionCampaignActionLog.objects.create(
            tenant=trigger.tenant,
            client=client,
            trigger=trigger,
            action_type=RetentionActionType.SENT,
            sent_at=now,
            ai_generated_body=ai_generated_body,
        )

        logger.info(
            f"Executed retention trigger '{trigger.name}' for client {client.id} "
            f"(Tenant: {trigger.tenant_id}, ActionLog: {action_log.id})"
        )
        return action_log

    @classmethod
    def evaluate_all_triggers_for_tenant(cls, tenant) -> int:
        """
        Evaluates all active trigger categories for a given tenant.
        Returns total action logs dispatched.
        """
        logs = []
        logs.extend(cls.evaluate_inactivity_triggers(tenant=tenant))
        logs.extend(cls.evaluate_failed_payment_triggers(tenant=tenant))
        logs.extend(cls.evaluate_expiry_triggers(tenant=tenant))
        logs.extend(cls.evaluate_custom_segment_triggers(tenant=tenant))
        return len(logs)

    @classmethod
    def evaluate_all_triggers_across_tenants(cls) -> int:
        """
        Iterates all active gym tenants and evaluates active retention triggers.
        """
        logger.info("Starting automated retention trigger evaluation across all active tenants...")
        total_actions = 0
        with bypass_tenant_isolation():
            active_tenants = Tenant.objects.filter(is_active=True)
            for tenant in active_tenants:
                try:
                    count = cls.evaluate_all_triggers_for_tenant(tenant)
                    total_actions += count
                    if count > 0:
                        logger.info(f"Dispatched {count} retention interventions for tenant {tenant.name} ({tenant.id})")
                except Exception as e:
                    logger.exception(f"Error evaluating retention triggers for tenant {tenant.id}: {e}")

        logger.info(f"Completed automated retention triggers scan: {total_actions} total interventions dispatched.")
        return total_actions
