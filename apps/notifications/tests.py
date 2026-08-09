from django.test import TestCase
from django.utils import timezone
from apps.users.models import User, UserRole
from apps.core.tenants.models import Tenant
from apps.notifications.models import (
    NotificationPriority, NotificationType, DeliveryPolicy,
    NotificationTemplate, NotificationPreference,
)
from apps.notifications.services import ChannelPolicyEngine, NotificationService
from apps.notifications.events import PaymentFailedEvent
from apps.notifications.templates_engine import TemplateRenderer

class ChannelPolicyEngineTests(TestCase):
    def test_derive_policy_normal(self):
        """NORMAL priority should be PUSH_ONLY."""
        policy = ChannelPolicyEngine.derive_policy(NotificationPriority.NORMAL)
        self.assertEqual(policy, DeliveryPolicy.PUSH_ONLY)

    def test_derive_policy_critical(self):
        """CRITICAL priority should force PUSH_AND_EMAIL."""
        policy = ChannelPolicyEngine.derive_policy(NotificationPriority.CRITICAL)
        self.assertEqual(policy, DeliveryPolicy.PUSH_AND_EMAIL)

    def test_derive_policy_critical_template(self):
        """NORMAL priority but template.is_critical=True should force PUSH_AND_EMAIL."""
        tenant = Tenant.objects.create(name="Test Gym")
        template = NotificationTemplate.objects.create(
            tenant=tenant,
            name="Test Template",
            notification_type=NotificationType.GENERAL,
            is_critical=True,
            title_template="Hello",
            body_template="World",
        )
        policy = ChannelPolicyEngine.derive_policy(NotificationPriority.NORMAL, template)
        self.assertEqual(policy, DeliveryPolicy.PUSH_AND_EMAIL)

    def test_is_mandatory(self):
        self.assertTrue(ChannelPolicyEngine.is_mandatory('EMERGENCY'))
        self.assertTrue(ChannelPolicyEngine.is_mandatory('SYSTEM'))
        self.assertFalse(ChannelPolicyEngine.is_mandatory('GENERAL'))

        tenant = Tenant.objects.create(name="Test Gym")
        template = NotificationTemplate.objects.create(
            tenant=tenant,
            name="Mandatory Template",
            notification_type=NotificationType.GENERAL,
            is_user_configurable=False,
            title_template="Hello",
            body_template="World",
        )
        self.assertTrue(ChannelPolicyEngine.is_mandatory('GENERAL', template))


class TemplateRendererTests(TestCase):
    def test_render_success(self):
        rendered = TemplateRenderer.render(
            "Hello {{client_name}}! Class is {{class_name}}.",
            {"client_name": "Alice", "class_name": "Yoga"}
        )
        self.assertEqual(rendered, "Hello Alice! Class is Yoga.")

    def test_render_missing_variable(self):
        rendered = TemplateRenderer.render(
            "Hello {{client_name}}! {{missing}}",
            {"client_name": "Alice"}
        )
        # Missing variable becomes empty string without crashing
        self.assertEqual(rendered, "Hello Alice! ")


class NotificationServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test Gym")
        self.user = User.objects.create_user(
            email="test@example.com",
            password="password",
            tenant=self.tenant,
            role=UserRole.CLIENT,
        )

    def test_system_default_payment_failed(self):
        """Payment failed event should default to CRITICAL and PUSH_AND_EMAIL."""
        event = PaymentFailedEvent(
            tenant_id=self.tenant.id,
            recipient_id=self.user.id,
            entity_id=None,
        )
        title, body, priority, notification_type, policy, is_mandatory = \
            NotificationService._system_defaults(event)

        self.assertEqual(priority, NotificationPriority.CRITICAL)
        self.assertEqual(policy, DeliveryPolicy.PUSH_AND_EMAIL)
        self.assertTrue(is_mandatory)

    def test_user_preference_opt_out(self):
        """User can opt out of non-mandatory notifications."""
        pref = NotificationPreference.get_or_create_for_user(self.user)
        pref.push_enabled = False
        pref.save()

        # This should abort early because push_enabled is False and it's not mandatory
        # We can test this by checking that no NotificationInbox is created.
        from apps.notifications.events import BookingConfirmedEvent
        event = BookingConfirmedEvent(
            tenant_id=self.tenant.id,
            recipient_id=self.user.id,
            entity_id=None,
        )
        NotificationService.handle_event(event)

        from apps.notifications.models import NotificationInbox
        self.assertEqual(NotificationInbox.all_objects.count(), 0)

    def test_user_preference_mandatory_bypass(self):
        """Mandatory notifications ignore user opt-out."""
        pref = NotificationPreference.get_or_create_for_user(self.user)
        pref.push_enabled = False
        pref.save()

        event = PaymentFailedEvent(
            tenant_id=self.tenant.id,
            recipient_id=self.user.id,
            entity_id=None,
        )
        NotificationService.handle_event(event)

        from apps.notifications.models import NotificationInbox
        self.assertEqual(NotificationInbox.all_objects.count(), 1)
