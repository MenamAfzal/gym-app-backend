"""
Notification Providers and Dispatcher

Provider Abstraction:
    PushProvider  → wraps existing FirebaseNotificationService (FCM HTTP v1 API)
    EmailProvider → wraps Django EmailMultiAlternatives

NotificationDispatcher:
    Single dispatch entry point. Reads delivery_policy from the inbox item
    and routes to the appropriate providers. Never independently decides
    whether to send email — that decision was already made by ChannelPolicyEngine
    at campaign creation time and stored on the inbox item.

Push delivery: per device (DeliveryRecord.device is set)
Email delivery: per recipient (DeliveryRecord.device is NULL, email_address is set)
"""
import logging
from django.utils import timezone
from django.core.mail import EmailMultiAlternatives
from django.conf import settings

logger = logging.getLogger(__name__)


# Push Provider

class PushProvider:
    """
    Wraps the existing FirebaseNotificationService for single-device push delivery.
    Returns (success: bool, response_data: dict).
    """

    @staticmethod
    def send(
        device_token: str,
        title: str,
        body: str,
        data: dict = None,
    ) -> tuple[bool, dict]:
        """
        Send a push notification to a single device token.

        Args:
            device_token: Firebase device registration token
            title: Notification title
            body: Notification body
            data: Optional custom data payload (will be stringified for FCM)

        Returns:
            (success, response_data)
            response_data keys: status, message_id, error_code, error_message
        """
        if data is None:
            data = {}

        try:
            from apps.notifications.utils import FirebaseNotificationService
            svc = FirebaseNotificationService()

            if not svc.project_id:
                logger.warning("PushProvider: Firebase project_id not configured. Push skipped.")
                return False, {
                    'status': 'failed',
                    'error_code': 'FIREBASE_NOT_CONFIGURED',
                    'error_message': 'Firebase credentials not configured.',
                }

            success = svc.send_fcm_message(device_token, title, body, data)

            if success:
                return True, {'status': 'sent', 'message_id': '', 'error_code': '', 'error_message': ''}
            else:
                return False, {
                    'status': 'failed',
                    'error_code': 'FCM_SEND_FAILED',
                    'error_message': 'FCM returned a non-200 response.',
                }

        except Exception as e:
            logger.error(f"PushProvider: Exception sending to {device_token[:20]}: {e}")
            return False, {
                'status': 'failed',
                'error_code': 'PROVIDER_EXCEPTION',
                'error_message': str(e),
            }


# Email Provider

class EmailProvider:
    """
    Wraps Django's email backend for notification email delivery.
    Uses the same EmailMultiAlternatives pattern as AuthService.send_email_otp().
    """

    @staticmethod
    def send(
        to_email: str,
        subject: str,
        body: str,
    ) -> tuple[bool, dict]:
        """
        Send a notification email to a single recipient.

        Args:
            to_email: Recipient email address
            subject: Email subject line
            body: Plain text body (also used as HTML fallback)

        Returns:
            (success, response_data)
        """
        try:
            import html as html_lib
            from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@example.com')

            msg = EmailMultiAlternatives(
                subject=subject,
                body=body,
                from_email=from_email,
                to=[to_email],
            )
            paragraphs_html = "".join(
                f"<p style='font-size:15px;color:#374151;line-height:1.6;margin:0 0 14px 0;'>{html_lib.escape(line)}</p>"
                for line in (body or "").split("\n\n")
                if line.strip()
            )
            html_content = (
                "<!DOCTYPE html><html><body style='margin:0;padding:0;background-color:#f4f6f9;"
                "font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'>"
                "<div style='width:100%;background-color:#f4f6f9;padding:30px 15px;box-sizing:border-box;'>"
                "<div style='max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;"
                "padding:32px;box-shadow:0 2px 8px rgba(0,0,0,0.06);'>"
                f"<h2 style='margin:0 0 20px 0;font-size:20px;font-weight:700;color:#111827;'>{html_lib.escape(subject)}</h2>"
                f"{paragraphs_html}"
                "</div></div></body></html>"
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=False)

            return True, {'status': 'sent', 'error_code': '', 'error_message': ''}

        except Exception as e:
            logger.error(f"EmailProvider: Failed to send to {to_email}: {e}")
            return False, {
                'status': 'failed',
                'error_code': 'EMAIL_SEND_FAILED',
                'error_message': str(e),
            }


# Notification Dispatcher

class NotificationDispatcher:
    """
    Dispatches a NotificationInbox item to all appropriate channels.

    Reads delivery_policy from the inbox item (set at creation by ChannelPolicyEngine).
    Does NOT independently decide whether to send email.

    Push: sends to all active FCM devices for the recipient in this tenant.
          Per-device DeliveryRecord created for each attempt.
          Invalid tokens (UNREGISTERED) → device deactivated, other devices continue.

    Email: sends to recipient's email address.
           Per-recipient (not per-device) DeliveryRecord created.
           Email failure does NOT affect push delivery state.
    """

    @staticmethod
    def dispatch(inbox_item) -> None:
        """
        Dispatch a NotificationInbox item to all configured channels.

        Args:
            inbox_item: NotificationInbox instance (must have recipient + tenant loaded)
        """
        from apps.notifications.models import (
            FCMDevice, DeliveryRecord, DeliveryPolicy, NotificationCampaign,
            NotificationPreference
        )
        from apps.notifications.services import ChannelPolicyEngine
        from django.db.models import F

        user   = inbox_item.recipient
        tenant = inbox_item.tenant

        push_sent_any  = False
        email_sent     = False

        pref = NotificationPreference.get_or_create_for_user(user)
        template = inbox_item.campaign.template if inbox_item.campaign_id else None
        is_mandatory = (
            ChannelPolicyEngine.is_mandatory(inbox_item.notification_type, template)
            or inbox_item.delivery_policy == DeliveryPolicy.PUSH_AND_EMAIL
        )

        should_send_push = is_mandatory or (pref.push_enabled and pref.is_type_enabled(inbox_item.notification_type))
        should_send_email = is_mandatory or (pref.email_enabled and pref.is_type_enabled(inbox_item.notification_type))

        if should_send_push and inbox_item.delivery_policy in [DeliveryPolicy.PUSH_ONLY, DeliveryPolicy.PUSH_AND_EMAIL]:
            devices = FCMDevice.all_objects.filter(user=user, tenant=tenant, active=True)

            if not devices.exists():
                logger.info(f"Dispatcher: No active FCM devices for user {user.id}")
            else:
                for device in devices:
                    record = DeliveryRecord(
                        inbox_item=inbox_item,
                        channel='push',
                        device=device,
                        status='pending',
                        attempted_at=timezone.now(),
                    )

                    success, response = PushProvider.send(
                        device_token=device.registration_id,
                        title=inbox_item.title,
                        body=inbox_item.body,
                        data={
                            'notification_id': str(inbox_item.id),
                            'action_payload': str(inbox_item.action_payload),
                            'notification_type': inbox_item.notification_type,
                        },
                    )

                    record.status               = 'sent' if success else 'failed'
                    record.provider_status      = response.get('status', '')
                    record.provider_message_id  = response.get('message_id', '')
                    record.error_code           = response.get('error_code', '')
                    record.error_message        = response.get('error_message', '')
                    record.save()

                    if success:
                        push_sent_any = True
                    elif 'UNREGISTERED' in response.get('error_code', ''):
                        # Token invalid — deactivate this device
                        device.active = False
                        device.save(update_fields=['active'])
                        logger.info(f"Dispatcher: Deactivated unregistered token for user {user.id}")

            # Update inbox item delivery state
            if push_sent_any != inbox_item.push_sent:
                inbox_item.push_sent = push_sent_any
                inbox_item.save(update_fields=['push_sent'])

            # Update campaign push counters (if linked to a campaign)
            if inbox_item.campaign_id and push_sent_any:
                NotificationCampaign.all_objects.filter(id=inbox_item.campaign_id).update(
                    push_sent_count=F('push_sent_count') + 1
                )
            elif inbox_item.campaign_id and not push_sent_any:
                NotificationCampaign.all_objects.filter(id=inbox_item.campaign_id).update(
                    push_failed_count=F('push_failed_count') + 1
                )

        if should_send_email and inbox_item.delivery_policy == DeliveryPolicy.PUSH_AND_EMAIL:
            record = DeliveryRecord(
                inbox_item=inbox_item,
                channel='email',
                device=None,                 
                email_address=user.email,
                status='pending',
                attempted_at=timezone.now(),
            )

            success, response = EmailProvider.send(
                to_email=user.email,
                subject=inbox_item.title,
                body=inbox_item.body,
            )

            record.status        = 'sent' if success else 'failed'
            record.provider_status = response.get('status', '')
            record.error_code    = response.get('error_code', '')
            record.error_message = response.get('error_message', '')
            record.save()

            email_sent = success

            # Update inbox item
            if email_sent != inbox_item.email_sent:
                inbox_item.email_sent = email_sent
                inbox_item.save(update_fields=['email_sent'])

            # Update campaign email counters
            if inbox_item.campaign_id:
                if email_sent:
                    NotificationCampaign.all_objects.filter(id=inbox_item.campaign_id).update(
                        email_sent_count=F('email_sent_count') + 1
                    )
                else:
                    NotificationCampaign.all_objects.filter(id=inbox_item.campaign_id).update(
                        email_failed_count=F('email_failed_count') + 1
                    )
