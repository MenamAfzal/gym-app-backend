"""
Notification Service Layer

The central entry point for ALL notification creation and delivery.

No other part of the application should directly create NotificationInbox records
or call PushProvider/EmailProvider. Everything flows through this service.

ChannelPolicyEngine:
    Single function that derives DeliveryPolicy from priority and template.
    CRITICAL or template.is_critical=True → PUSH_AND_EMAIL
    Everything else → PUSH_ONLY
    This is the ONLY place this decision is made.

NotificationService:
    create_campaign() → creates a NotificationCampaign (DRAFT)
    send_campaign()   → triggers async dispatch via Celery
    cancel_campaign() → cancels scheduled campaign, revokes Celery task
    handle_event()    → handles domain events from platform code
"""
import uuid
import logging
from django.db import transaction
from django.utils import timezone
from django.db.models import Q

logger = logging.getLogger(__name__)


# Channel Policy Engine

class ChannelPolicyEngine:
    """
    Single source of truth for channel (delivery policy) selection.

    Rules:
    1. CRITICAL priority → PUSH_AND_EMAIL
    2. template.is_critical=True → PUSH_AND_EMAIL
    3. Everything else → PUSH_ONLY

    Mandatory types always delivered, even if user opts out:
    - EMERGENCY, SYSTEM, and any template with is_user_configurable=False

    Note: This function is called at campaign/inbox creation time.
    The result is stored on the model — never re-derived at delivery time.
    """

    MANDATORY_CRITICAL_TYPES = {
        'EMERGENCY',
        'SYSTEM',
    }

    @classmethod
    def derive_policy(cls, priority: str, template=None) -> str:
        """
        Derive DeliveryPolicy from priority and optional template.

        Args:
            priority: NotificationPriority value
            template: Optional NotificationTemplate instance

        Returns:
            DeliveryPolicy value ('PUSH_ONLY' or 'PUSH_AND_EMAIL')
        """
        from apps.notifications.models import NotificationPriority, DeliveryPolicy

        if priority == NotificationPriority.CRITICAL:
            return DeliveryPolicy.PUSH_AND_EMAIL

        if template and getattr(template, 'is_critical', False):
            return DeliveryPolicy.PUSH_AND_EMAIL

        return DeliveryPolicy.PUSH_ONLY

    @classmethod
    def is_mandatory(cls, notification_type: str, template=None) -> bool:
        """
        Returns True if this notification must be delivered regardless of user opt-out.

        Args:
            notification_type: NotificationType value
            template: Optional NotificationTemplate instance

        Returns:
            bool
        """
        if notification_type in cls.MANDATORY_CRITICAL_TYPES:
            return True
        if template and not getattr(template, 'is_user_configurable', True):
            return True
        return False


# Notification Service

class NotificationService:
    """
    Central service for all notification operations.

    create_campaign → NotificationCampaign (DRAFT status)
    send_campaign   → enqueues dispatch_campaign_task via Celery
    cancel_campaign → cancels + revokes Celery task
    handle_event    → processes domain events from platform code
    """

    @staticmethod
    @transaction.atomic
    def create_campaign(tenant, created_by, validated_data: dict):
        """
        Create a NotificationCampaign in DRAFT status.

        Channel policy (delivery_policy) is derived here from priority + template.
        The campaign owner cannot set PUSH_AND_EMAIL for a NORMAL notification.

        Args:
            tenant: Tenant instance
            created_by: User instance (creator)
            validated_data: dict from CampaignCreateSerializer.validated_data

        Returns:
            NotificationCampaign instance
        """
        from apps.notifications.models import (
            NotificationCampaign, NotificationStatus, NotificationSource,
            NotificationRecurrenceRule,
        )

        template = validated_data.get('template')
        priority = validated_data.get('priority', 'NORMAL')

        # Derive channel policy — owner cannot override this
        delivery_policy = ChannelPolicyEngine.derive_policy(priority, template)

        # Handle recurrence rule creation
        recurrence_rule = None
        recurrence_data = validated_data.pop('recurrence_rule_data', None)
        if recurrence_data:
            recurrence_rule = NotificationRecurrenceRule.objects.create(
                tenant=tenant,
                **recurrence_data,
            )

        campaign = NotificationCampaign.objects.create(
            tenant=tenant,
            created_by=created_by,
            title=validated_data['title'],
            body=validated_data['body'],
            notification_type=validated_data['notification_type'],
            priority=priority,
            source=NotificationSource.CAMPAIGN,
            delivery_policy=delivery_policy,
            status=NotificationStatus.DRAFT,
            audience_type=validated_data['audience_type'],
            audience_group=validated_data.get('audience_group'),
            audience_entity_id=validated_data.get('audience_entity_id'),
            audience_filter=validated_data.get('audience_filter', {}),
            scheduled_at=validated_data.get('scheduled_at'),
            recurrence_rule=recurrence_rule,
            bypass_quiet_hours=validated_data.get('bypass_quiet_hours', False),
            action_payload=validated_data.get('action_payload', {}),
            template=template,
            idempotency_key=str(uuid.uuid4()),
        )

        # Handle M2M audience_users after creation
        audience_users = validated_data.get('audience_users', [])
        if audience_users:
            # Validate all users belong to this tenant
            valid_user_ids = [u.id for u in audience_users if u.tenant_id == tenant.id]
            campaign.audience_users.set(valid_user_ids)

        return campaign

    @staticmethod
    def send_campaign(campaign) -> None:
        """
        Trigger immediate or scheduled delivery of a campaign.

        If campaign.scheduled_at is set, sets status=SCHEDULED and next_run_at.
        If scheduled_at is None, sends immediately.

        Handles quiet hours: if scheduled_at falls in quiet window → delay.
        Uses transaction.on_commit to prevent task from running before DB commit.

        Args:
            campaign: NotificationCampaign instance
        """
        from apps.notifications.models import NotificationStatus

        if campaign.status == NotificationStatus.CANCELLED:
            raise ValueError("Cannot send a cancelled campaign.")

        if campaign.status in [NotificationStatus.PROCESSING, NotificationStatus.SENT]:
            raise ValueError(f"Campaign is already {campaign.status}.")

        if campaign.scheduled_at:
            # Scheduled — check quiet hours
            send_at = NotificationService._apply_quiet_hours(campaign, campaign.scheduled_at)
            campaign.scheduled_at = send_at
            campaign.next_run_at  = send_at
            campaign.status       = NotificationStatus.SCHEDULED
            campaign.save(update_fields=['scheduled_at', 'next_run_at', 'status'])
            logger.info(f"Campaign {campaign.id} scheduled for {send_at}")
        else:
            # Immediate — enqueue immediately
            campaign.status = NotificationStatus.SCHEDULED
            campaign.next_run_at = timezone.now()
            campaign.save(update_fields=['status', 'next_run_at'])

            transaction.on_commit(
                lambda: NotificationService._enqueue_dispatch(campaign)
            )

    @staticmethod
    def _enqueue_dispatch(campaign) -> None:
        from apps.notifications.tasks import dispatch_campaign_task
        result = dispatch_campaign_task.apply_async(
            args=[str(campaign.id)],
            countdown=0,
        )
        campaign.celery_task_id = result.id
        campaign.save(update_fields=['celery_task_id'])

    @staticmethod
    def cancel_campaign(campaign) -> None:
        """
        Cancel a SCHEDULED or DRAFT campaign.
        Revokes the Celery task if one was queued.

        Args:
            campaign: NotificationCampaign instance
        """
        from apps.notifications.models import NotificationStatus

        if campaign.status in [NotificationStatus.SENT, NotificationStatus.PROCESSING]:
            raise ValueError(f"Cannot cancel a campaign with status '{campaign.status}'.")

        if campaign.status == NotificationStatus.CANCELLED:
            return  # Already cancelled — idempotent

        campaign.status = NotificationStatus.CANCELLED
        campaign.save(update_fields=['status'])

        # Revoke Celery task if it was queued
        if campaign.celery_task_id:
            try:
                from config.celery import app as celery_app
                celery_app.control.revoke(campaign.celery_task_id, terminate=True)
                logger.info(f"Revoked Celery task {campaign.celery_task_id} for campaign {campaign.id}")
            except Exception as e:
                logger.warning(f"Could not revoke Celery task for campaign {campaign.id}: {e}")

    @staticmethod
    def handle_event(event) -> None:
        """
        Handle a domain event from platform code.

        This is the single entry point for all automated/system notifications.
        Platform code emits a NotificationEvent — this method handles delivery.

        Steps:
        1. Look up active NotificationAutomation for this tenant + event_type
        2. Render template with TemplateRenderer (or use system default)
        3. Check user's NotificationPreference (unless mandatory)
        4. Idempotency check — skip if already sent
        5. Create NotificationInbox
        6. transaction.on_commit → deliver_inbox_task

        Args:
            event: NotificationEvent instance
        """
        from apps.notifications.models import (
            NotificationAutomation, NotificationInbox,
            NotificationPreference, NotificationSource,
            TenantNotificationSettings,
        )
        from apps.notifications.templates_engine import TemplateRenderer
        from apps.core.tenants.models import Tenant
        from apps.users.models import User

        try:
            tenant    = Tenant.objects.get(id=event.tenant_id)
            recipient = User.objects.get(id=event.recipient_id)
        except (Tenant.DoesNotExist, User.DoesNotExist) as e:
            logger.error(f"NotificationService.handle_event: {e}")
            return

        # Look up active automation for this event trigger
        automation = NotificationAutomation.all_objects.filter(
            tenant=tenant,
            event_trigger=event.event_type,
            is_active=True,
        ).select_related('template').first()

        if automation:
            template = automation.template
            title, body = TemplateRenderer.render_pair(
                template.title_template,
                template.body_template,
                event.context_data or {},
            )
            priority        = template.priority
            notification_type = template.notification_type
            delivery_policy = ChannelPolicyEngine.derive_policy(priority, template)
            is_mandatory    = ChannelPolicyEngine.is_mandatory(notification_type, template)
        else:
            # No automation configured — use sensible system defaults
            title, body, priority, notification_type, delivery_policy, is_mandatory = \
                NotificationService._system_defaults(event)

        # Check user notification preference (unless mandatory)
        if not is_mandatory:
            pref = NotificationPreference.get_or_create_for_user(recipient)
            if not pref.push_enabled:
                logger.debug(f"User {recipient.id} has push disabled — skipping {event.event_type}")
                return
            if not pref.is_type_enabled(notification_type):
                logger.debug(f"User {recipient.id} has {notification_type} disabled — skipping")
                return

        # Idempotency check — prevent duplicate notifications on retries
        entity_id_str = str(event.entity_id) if event.entity_id else 'none'
        idempotency_key = f"{event.event_type}:{event.recipient_id}:{entity_id_str}"

        if NotificationInbox.all_objects.filter(idempotency_key=idempotency_key).exists():
            logger.debug(f"Notification already sent for key: {idempotency_key} — skipping")
            return

        # Create inbox item
        with transaction.atomic():
            inbox_item = NotificationInbox.objects.create(
                tenant=tenant,
                recipient=recipient,
                title=title,
                body=body,
                notification_type=notification_type,
                priority=priority,
                source=NotificationSource.SYSTEM if not automation else NotificationSource.AUTOMATION,
                delivery_policy=delivery_policy,
                action_payload=event.context_data or {},
                idempotency_key=idempotency_key,
            )

            transaction.on_commit(
                lambda iid=inbox_item.id: NotificationService._enqueue_deliver(iid)
            )

    @staticmethod
    def _enqueue_deliver(inbox_item_id) -> None:
        from apps.notifications.tasks import deliver_inbox_task
        deliver_inbox_task.delay(str(inbox_item_id))

    @staticmethod
    def _system_defaults(event) -> tuple:
        """
        System default content when no automation is configured for an event.
        Returns: (title, body, priority, notification_type, delivery_policy, is_mandatory)
        """
        from apps.notifications.models import (
            NotificationPriority, NotificationType, DeliveryPolicy
        )

        DEFAULTS = {
            'booking_confirmed': (
                'Booking Confirmed',
                'Your class booking has been confirmed.',
                NotificationPriority.NORMAL,
                NotificationType.BOOKING,
                DeliveryPolicy.PUSH_ONLY,
                False,
            ),
            'booking_cancelled': (
                'Booking Cancelled',
                'Your class booking has been cancelled.',
                NotificationPriority.NORMAL,
                NotificationType.BOOKING,
                DeliveryPolicy.PUSH_ONLY,
                False,
            ),
            'waitlist_offered': (
                'Spot Available!',
                'A spot has opened in a class you were waiting for.',
                NotificationPriority.HIGH,
                NotificationType.WAITLIST,
                DeliveryPolicy.PUSH_ONLY,
                False,
            ),
            'session_cancelled': (
                'Class Cancelled',
                'A class you were booked in has been cancelled. Your credits have been refunded.',
                NotificationPriority.HIGH,
                NotificationType.CLASS,
                DeliveryPolicy.PUSH_ONLY,
                False,
            ),
            'class_reminder_24h': (
                'Class Tomorrow',
                "Don't forget — you have a class tomorrow.",
                NotificationPriority.NORMAL,
                NotificationType.CLASS,
                DeliveryPolicy.PUSH_ONLY,
                False,
            ),
            'class_reminder_1h': (
                'Class in 1 Hour',
                'Your class starts in 1 hour.',
                NotificationPriority.NORMAL,
                NotificationType.CLASS,
                DeliveryPolicy.PUSH_ONLY,
                False,
            ),
            'appointment_reminder': (
                'Appointment Reminder',
                'You have an upcoming appointment.',
                NotificationPriority.NORMAL,
                NotificationType.APPOINTMENT,
                DeliveryPolicy.PUSH_ONLY,
                False,
            ),
            'workout_assigned': (
                'New Workout Assigned',
                'Your trainer has assigned you a new workout.',
                NotificationPriority.NORMAL,
                NotificationType.WORKOUT,
                DeliveryPolicy.PUSH_ONLY,
                False,
            ),
            'payment_success': (
                'Payment Successful',
                'Your payment was successfully processed.',
                NotificationPriority.NORMAL,
                NotificationType.PAYMENT,
                DeliveryPolicy.PUSH_ONLY,
                False,
            ),
            'payment_failed': (
                'Payment Failed',
                'Your payment failed. Please update your payment method.',
                NotificationPriority.CRITICAL,
                NotificationType.PAYMENT,
                DeliveryPolicy.PUSH_AND_EMAIL,
                True,   # Mandatory — ignores user preference opt-out
            ),
            'membership_expiring': (
                'Membership Expiring Soon',
                'Your membership is expiring soon. Renew to continue training.',
                NotificationPriority.HIGH,
                NotificationType.MEMBERSHIP,
                DeliveryPolicy.PUSH_ONLY,
                False,
            ),
        }

        return DEFAULTS.get(
            event.event_type,
            (
                'Notification',
                'You have a new notification.',
                NotificationPriority.NORMAL,
                NotificationType.GENERAL,
                DeliveryPolicy.PUSH_ONLY,
                False,
            )
        )

    @staticmethod
    def _apply_quiet_hours(campaign, scheduled_at):
        """
        Check if scheduled_at falls in quiet hours window and delay if needed.

        Returns: adjusted datetime (delayed to end of quiet window if applicable)
        """
        from apps.notifications.models import NotificationPriority, TenantNotificationSettings
        import pytz

        try:
            tn_settings = TenantNotificationSettings.get_or_create_for_tenant(campaign.tenant)
        except Exception:
            return scheduled_at

        if not tn_settings.quiet_hours_enabled:
            return scheduled_at

        if campaign.bypass_quiet_hours:
            return scheduled_at

        if campaign.priority == NotificationPriority.CRITICAL and tn_settings.quiet_hours_bypass_critical:
            return scheduled_at

        if not tn_settings.quiet_hours_start or not tn_settings.quiet_hours_end:
            return scheduled_at

        # Convert scheduled_at to tenant timezone for comparison
        try:
            import pytz
            tz = pytz.timezone(tn_settings.timezone)
            local_dt = scheduled_at.astimezone(tz)
            local_time = local_dt.time()

            q_start = tn_settings.quiet_hours_start
            q_end   = tn_settings.quiet_hours_end

            in_quiet = False
            if q_start <= q_end:
                # Same day window (e.g. 01:00 - 06:00)
                in_quiet = q_start <= local_time < q_end
            else:
                # Spans midnight (e.g. 22:00 - 07:00)
                in_quiet = local_time >= q_start or local_time < q_end

            if in_quiet:
                # Delay to the end of the quiet window (next day if needed)
                from datetime import datetime, timedelta
                if local_time >= q_start:
                    # Quiet window started today — end is next day
                    next_day = local_dt.date() + timedelta(days=1)
                    end_naive = datetime.combine(next_day, q_end)
                else:
                    # Still in quiet window from previous night
                    end_naive = datetime.combine(local_dt.date(), q_end)

                end_local = tz.localize(end_naive)
                delayed_utc = end_local.astimezone(pytz.utc)
                logger.info(
                    f"Campaign {campaign.id}: Scheduled during quiet hours. "
                    f"Delayed from {scheduled_at} to {delayed_utc}"
                )
                return delayed_utc

        except Exception as e:
            logger.warning(f"Quiet hours check failed for campaign {campaign.id}: {e}")

        return scheduled_at
