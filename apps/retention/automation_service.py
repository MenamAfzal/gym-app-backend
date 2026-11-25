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
    TenantOnboardingSettings,
)
from .ai_service import RetentionAIService
from .services import SegmentQueryService

logger = logging.getLogger(__name__)


DEFAULT_ONBOARDING_EMAIL_TEMPLATES = [
    {
        "day": 0,
        "name": "Day 0 - Welcome to the Studio",
        "subject": "Welcome to {{studio_name}}",
        "body": (
            "Hi {{first_name}},\n\n"
            "Welcome to {{studio_name}}, we're really glad you've joined us.\n\n"
            "Starting something new can feel like a big step, especially if fitness hasn't always felt straightforward. "
            "That's exactly why {{studio_name}} exists, to make things feel simple, supported and easy to follow from the very beginning.\n\n"
            "You've already taken the hardest step by getting started. From here, we'll guide you every step of the way.\n\n"
            "What to focus on first\n\n"
            "Right now, there's nothing complicated to think about.\n"
            "Your focus is simply to:\n"
            "• Turn up\n"
            "• Follow the session\n"
            "• Take things at your own pace\n\n"
            "Everything is fully guided, so you'll always know what to do, and our team will be there to support you throughout.\n\n"
            "Your first few sessions\n\n"
            "Over your first visits, we'll help you:\n"
            "• Get comfortable with the sessions\n"
            "• Understand how everything works\n"
            "• Build a routine that feels manageable\n\n"
            "There's no pressure to get everything perfect. Just showing up consistently is what matters most.\n\n"
            "Your welcome guide\n\n"
            "To help you feel confident from day one, we've put together a simple guide that explains how everything works and how to get the most from your membership:\n\n"
            "👉 {{welcome_guide_link}}\n\n"
            "It will walk you through:\n"
            "• How the sessions work\n"
            "• What to expect from your sessions\n"
            "• Simple habits to support your progress\n"
            "• How we support you throughout your journey\n\n"
            "We're here to support you\n\n"
            "If you ever feel unsure about anything, just ask. You don't need to figure this out on your own. "
            "We're here to help you build confidence, answer questions, and support you as you go.\n\n"
            "Your membership details\n\n"
            "Thank you for joining as a member on the following terms; {{contract_length}} commencing on the {{start_date}}.\n"
            "Your membership number is: {{membership_number}}\n"
            "(You may need this for access. It's also available in your Member App.)\n\n"
            "Manage your account here:\n"
            "{{manage_online_link}}\n\n"
            "Membership:\n"
            "{{membership_name}}\n"
            "Payment Schedule (if applicable):\n"
            "{{payment_schedule}}\n\n"
            "By joining, you agree to use the facilities (&/or systems) provided by {{studio_name}} in accordance with our Terms and Conditions, available in your member app and publicly.\n\n"
            "If your membership is dependent on your health and fitness, you confirm that you know of no medical or physical reason why you should not safely participate in an exercise programme prescribed by {{studio_name}} and/or its instructors, and you do so at your own risk.\n\n"
            "If you have chosen a recurring membership, once the minimum term is complete, it will continue on a rolling basis.\n\n"
            "We're really looking forward to supporting you over the coming weeks.\n\n"
            "Take your time, keep things simple, and we'll guide you every step of the way.\n\n"
            "Speak soon,\n"
            "{{studio_name}}"
        ),
    },
    {
        "day": 2,
        "name": "Day 2 - How your first sessions should feel",
        "subject": "How your first sessions should feel",
        "body": (
            "Hi {{first_name}},\n\n"
            "Just a quick check-in as you get started at {{studio_name}}.\n"
            "One of the most common questions people have early on is:\n\n"
            "\"Is this how it's meant to feel?\"\n\n"
            "You might notice things like:\n"
            "• Muscles working in a way you're not used to\n"
            "• Feeling tired, even after a short session\n"
            "• Movements or areas being activated that you don't normally feel\n\n"
            "That's completely normal.\n\n"
            "The way our sessions at {{studio_name}} are designed means your body is working efficiently in a short space of time. "
            "That's why recovery between sessions is just as important as the training itself.\n\n"
            "For many people, this is a different experience to what they've tried before. It's not about long, exhausting workouts, "
            "it's about building strength, improving fitness, and creating a routine that feels manageable and sustainable.\n\n"
            "A simple way to support your progress\n\n"
            "Alongside your sessions, a few small habits can make a big difference.\n\n"
            "We've put together a simple Healthy Habits Guide to help you with things like:\n"
            "• How often to train\n"
            "• Eating in a way that supports your goals\n"
            "• Hydration and recovery\n"
            "• Building a routine that sticks\n\n"
            "👉 {{healthy_habits_guide_link}}\n\n"
            "If anything ever feels unclear, just ask.\n\n"
            "You don't need to figure this out on your own, we're here to guide you every step of the way.\n\n"
            "Speak soon,\n"
            "{{studio_name}}"
        ),
    },
    {
        "day": 7,
        "name": "Day 7 - How are you feeling?",
        "subject": "How are you feeling?",
        "body": (
            "Hi {{first_name}},\n\n"
            "We just wanted to check in as you complete your first week at {{studio_name}}.\n\n"
            "By now, you might be noticing a few changes. It's quite common to feel a little sore or more tired than usual, "
            "especially if your body isn't used to this type of training yet.\n\n"
            "That's completely normal.\n\n"
            "It's simply your body adapting, and a sign that things are starting to work.\n\n"
            "The good news is, this feeling won't last. As your body gets used to the sessions, things will begin to feel more comfortable and natural.\n\n"
            "A few simple tips to help\n\n"
            "• Gentle stretching can help ease any tightness\n"
            "• Stay well hydrated throughout the day\n"
            "• Keep your sessions consistent, even if you take them at a steady pace\n\n"
            "There's no need to push too hard. Just keep showing up and let your body adjust.\n\n"
            "You've completed your first week\n\n"
            "That's a great start.\n\n"
            "Building a routine takes time, but you're already on your way. It typically takes around 6 weeks to form a habit, "
            "and this first week is an important step towards that.\n\n"
            "Keep things simple, stay consistent, and trust the process.\n\n"
            "We're here to support you every step of the way.\n\n"
            "Speak soon,\n"
            "{{studio_name}}\n\n"
            "P.S. Check out some of our client testimonials and see for yourself what sticking to the plan looks like:\n"
            "{{testimonials_link}}"
        ),
    },
    {
        "day": 14,
        "name": "Day 14 - It should start to feel a bit different now",
        "subject": "It should start to feel a bit different now",
        "body": (
            "Hi {{first_name}},\n\n"
            "Around this point, we often hear something like:\n\n"
            "\"I didn't expect it to feel like this.\"\n\n"
            "Not because it's easy, but because it feels more manageable, more structured, and easier to stay consistent with than people expected.\n\n"
            "By now, you've probably started to notice that {{studio_name}} isn't about long or exhausting workouts. "
            "It's about focused sessions, simple routines, and building something that fits into your life.\n\n"
            "For many people, this is where things begin to feel a bit more natural.\n\n"
            "A quick story from one of our members\n\n"
            "Here's a short video from some of our members who have achieved some great things:\n\n"
            "👉 Watch their experience\n"
            "{{testimonial_video_link}}\n\n"
            "You might have already mentioned it to someone\n\n"
            "At this stage, it's quite common for members to start talking to friends or family about what they're doing, "
            "usually because they're surprised by how different it feels compared to anything they've tried before.\n\n"
            "If someone comes to mind, they're always welcome to come in and try a session for themselves.\n\n"
            "There's no pressure at all, just a chance for them to see how it works and decide if it's right for them.\n\n"
            "If you'd like to invite someone along, just mention it to the team or reply to this email and we'll take care of everything.\n\n"
            "If not, that's absolutely fine too.\n\n"
            "Just keep doing what you're doing. Staying consistent is what really makes the difference.\n\n"
            "Speak soon,\n"
            "{{studio_name}}"
        ),
    },
    {
        "day": 21,
        "name": "Day 21 - A small shift that makes a big difference",
        "subject": "A small shift that makes a big difference",
        "body": (
            "Hi {{first_name}},\n\n"
            "By now, you've likely started to settle into a bit of a routine with your training.\n\n"
            "At this stage, one of the biggest things that can support your progress isn't just the sessions themselves, "
            "it's how you're fuelling your body alongside them.\n\n"
            "Achieving results is always a combination of movement and lifestyle, and nutrition plays a really important role in how you feel, recover, and progress.\n\n"
            "The good news is, it doesn't need to be complicated.\n\n"
            "A simple guide to help\n\n"
            "We've put together a straightforward nutrition guide that breaks down what \"eating well\" actually looks like in a realistic and manageable way.\n\n"
            "👉 {{nutrition_guide_link}}\n\n"
            "It covers:\n"
            "• Building balanced meals\n"
            "• Simple habits that make a difference\n"
            "• How to support your energy and recovery\n"
            "• Keeping things sustainable rather than restrictive\n\n"
            "Keep it simple\n\n"
            "There's no need to overhaul everything at once.\n\n"
            "Small, consistent improvements will always take you further than trying to be perfect for a short period of time.\n\n"
            "You're already building the training side of your routine. This is just about supporting that in a way that feels achievable.\n\n"
            "If you have any questions around nutrition or anything else, just speak to the team. We're here to help.\n"
            "Keep going, you're doing really well.\n\n"
            "Speak soon,\n"
            "{{studio_name}}"
        ),
    },
    {
        "day": 28,
        "name": "Day 28 - You've made it through your first month",
        "subject": "You've made it through your first month",
        "body": (
            "Hi {{first_name}},\n\n"
            "You're now around a month into your journey at {{studio_name}}, and that's a bigger step than most people realise.\n\n"
            "A lot of people never get this far. Simply showing up consistently over the past few weeks has already put you in a really strong position.\n\n"
            "Around this stage, it's quite common to start noticing small changes, such as:\n"
            "• Feeling a bit stronger or more capable\n"
            "• Moving more comfortably through the sessions\n"
            "• Having more energy day to day\n"
            "• Training starting to feel like part of your routine\n\n"
            "Not everything will feel dramatic, and that's completely normal. The real progress comes from staying consistent and letting things build over time.\n\n"
            "A good time to check in\n\n"
            "If you haven't already, this is a great point to have a quick chat with the team.\n\n"
            "We can talk through how things are feeling, answer any questions, and make any small adjustments to keep things moving in the right direction.\n\n"
            "Keep doing what's working\n\n"
            "There's no need to change everything or push harder.\n\n"
            "Just keep showing up, keep things simple, and trust the process.\n\n"
            "You've made a great start.\n\n"
            "A quick reminder\n\n"
            "If you know someone who might enjoy the same experience, they're always welcome to come in and try a session for themselves. "
            "No pressure, just a chance to see how it feels. Simply send them this link:\n\n"
            "{{referral_link}}\n\n"
            "Well done for getting this far, keep going.\n\n"
            "Speak soon,\n"
            "{{studio_name}}"
        ),
    },
    {
        "day": 42,
        "name": "Day 42 - This is where it becomes a habit",
        "subject": "This is where it becomes a habit",
        "body": (
            "Hi {{first_name}},\n\n"
            "Just a quick note to say well done.\n\n"
            "You're now around six weeks into your journey at {{studio_name}}, and that really matters.\n\n"
            "There's a reason we recognise this point. It typically takes around six weeks to build a genuine habit, "
            "and many people never quite get this far. Life gets busy, routines change, and it's easy to fall off track.\n"
            "You didn't.\n\n"
            "By continuing to show up, you've done more than just attend sessions, you've started to build something consistent. "
            "And consistency is what really makes the difference over time.\n\n"
            "What you might be noticing now\n\n"
            "Around this stage, many members find that:\n"
            "• The sessions feel more familiar and less daunting\n"
            "• Fitting training into the week becomes easier\n"
            "• Their body feels a bit stronger and more responsive\n\n"
            "These are all signs that things are moving in the right direction.\n\n"
            "Keep supporting what you're building\n\n"
            "Alongside your training, small lifestyle habits will continue to support your progress.\n\n"
            "If you'd like a bit of extra inspiration, we've put together a simple recipe guide with ideas that are easy to follow and fit into everyday life:\n\n"
            "👉 {{recipe_guide_link}}\n\n"
            "A good time to check in\n\n"
            "If anything feels like it needs adjusting, this is a great time to speak to the team. Small changes now can make a big difference as you move forward.\n\n"
            "You've made a great start, and more importantly, you've built something consistent.\n\n"
            "Keep doing what you're doing, it's working.\n\n"
            "Speak soon,\n"
            "{{studio_name}}"
        ),
    },
    {
        "day": 70,
        "name": "Day 70 - 10 weeks in – time to celebrate what you've built",
        "subject": "10 weeks in – time to celebrate what you've built",
        "body": (
            "Hi {{first_name}},\n\n"
            "You're now around 10 weeks into your journey at {{studio_name}}, and that's something worth recognising.\n\n"
            "Very few people stay consistent for this length of time, especially with everything else life brings. "
            "Reaching this point says a lot about the routine you've built.\n\n"
            "By now, many members start to notice:\n"
            "• Feeling more comfortable and confident in their sessions\n"
            "• Moving more easily and with more control\n"
            "• Training becoming part of their lifestyle, rather than something they have to think about\n\n"
            "Not everything will feel dramatic, but the consistency you've built is what really matters.\n\n"
            "Take a moment to recognise it\n\n"
            "This is a great point to acknowledge the effort you've put in.\n\n"
            "That might be something simple like:\n"
            "• Planning something to look forward to\n"
            "• Treating yourself to something you've had in mind\n"
            "• Taking a bit of time to reflect on how far you've come\n\n"
            "Not as a reward for being perfect, just as recognition of the consistency you've built.\n\n"
            "Looking ahead\n\n"
            "If you haven't already, this is a great time to have a quick chat with the team.\n\n"
            "We can review how things are feeling and look at what you'd like to focus on next, whether that's building strength, "
            "improving fitness, or simply continuing to stay consistent.\n\n"
            "You've built something really solid here.\n\n"
            "The next phase is simply about continuing what's already working.\n\n"
            "Well done, and keep going.\n\n"
            "Speak soon,\n"
            "{{studio_name}}"
        ),
    },
]


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
    def seed_default_onboarding_triggers(cls, tenant) -> List[RetentionCampaignTrigger]:
        """
        Idempotently seeds the 7 Studio 35-style onboarding email triggers
        (Day 0, Day 2, Day 7, Day 14, Day 21, Day 28, Day 42, Day 70) for a tenant.
        Also ensures TenantOnboardingSettings exists for the tenant.
        """
        created_triggers = []
        with bypass_tenant_isolation():
            TenantOnboardingSettings.get_or_create_for_tenant(tenant)
            for item in DEFAULT_ONBOARDING_EMAIL_TEMPLATES:
                day = item["day"]
                existing = RetentionCampaignTrigger.all_objects.filter(
                    tenant=tenant,
                    trigger_type=RetentionTriggerType.ONBOARDING_MILESTONE,
                    trigger_value=day,
                ).first()
                if not existing:
                    trig = RetentionCampaignTrigger.objects.create(
                        tenant=tenant,
                        name=item["name"],
                        is_active=True,
                        trigger_type=RetentionTriggerType.ONBOARDING_MILESTONE,
                        trigger_value=day,
                        channel=RetentionChannel.EMAIL,
                        template_subject=item["subject"],
                        template_body=item["body"],
                        use_ai_personalization=False,
                    )
                    created_triggers.append(trig)
        return created_triggers

    @classmethod
    def _build_onboarding_context_tokens(cls, client: User, tenant) -> dict:
        """
        Resolves all dynamic variables used across the Day 0–70 onboarding email series,
        supporting both {{variable}} and [BracketVariable] notations from the Studio 35 PDF.
        """
        profile = getattr(client, 'profile', None)
        first_name = (
            client.first_name
            or (getattr(profile, 'first_name', '') if profile else '')
            or client.full_name
            or (client.email.split('@')[0] if client.email else 'there')
        ).strip()
        full_name = (
            client.full_name
            or (f"{getattr(profile, 'first_name', '')} {getattr(profile, 'last_name', '')}".strip() if profile else '')
            or first_name
        ).strip()
        studio_name = tenant.name if tenant else "Our Studio"

        settings_obj = TenantOnboardingSettings.all_objects.filter(tenant=tenant).first()

        latest_pkg = (
            Package.all_objects.filter(tenant=tenant, client=client)
            .select_related('package_type')
            .order_by('-purchased_at', '-created_at')
            .first()
        )
        if latest_pkg and latest_pkg.package_type:
            pt = latest_pkg.package_type
            membership_name = pt.name
            start_dt = latest_pkg.purchased_at or latest_pkg.created_at or client.date_joined
            start_date_str = start_dt.strftime("%d %b %Y") if start_dt else timezone.now().strftime("%d %b %Y")
            validity_days = pt.validity_days or 30
            if validity_days % 30 == 0 and validity_days >= 30:
                months = validity_days // 30
                contract_length = f"{months} Month{'s' if months > 1 else ''}"
            else:
                contract_length = f"{validity_days} Days"
            price_val = latest_pkg.price if latest_pkg.price is not None else pt.price
            currency_code = (getattr(tenant, 'currency', 'gbp') or 'gbp').upper()
            cycle_label = pt.billing_cycle or 'monthly'
            payment_schedule = f"{currency_code} {price_val} ({cycle_label})"
        else:
            membership_name = "Standard Membership"
            start_dt = client.date_joined or timezone.now()
            start_date_str = start_dt.strftime("%d %b %Y")
            contract_length = "Rolling Monthly"
            payment_schedule = "As per your selected membership plan"

        membership_number = f"MEM-{str(client.id).split('-')[0].upper()}"
        default_app_link = f"fitverx://login?tenant_id={tenant.id}" if tenant else "fitverx://login"

        welcome_guide = (settings_obj.welcome_guide_url if settings_obj and settings_obj.welcome_guide_url else default_app_link)
        healthy_habits = (settings_obj.healthy_habits_guide_url if settings_obj and settings_obj.healthy_habits_guide_url else default_app_link)
        testimonials = (settings_obj.testimonials_url if settings_obj and settings_obj.testimonials_url else default_app_link)
        testimonial_video = (settings_obj.testimonial_video_url if settings_obj and settings_obj.testimonial_video_url else testimonials)
        nutrition_guide = (settings_obj.nutrition_guide_url if settings_obj and settings_obj.nutrition_guide_url else default_app_link)
        referral_link = (settings_obj.referral_url if settings_obj and settings_obj.referral_url else default_app_link)
        recipe_guide = (settings_obj.recipe_guide_url if settings_obj and settings_obj.recipe_guide_url else default_app_link)
        manage_online = (settings_obj.manage_account_url if settings_obj and settings_obj.manage_account_url else default_app_link)

        return {
            "{{first_name}}": first_name,
            "{first_name}": first_name,
            "[FirstName]": first_name,
            "{{name}}": full_name,
            "{name}": full_name,
            "{{studio_name}}": studio_name,
            "{studio_name}": studio_name,
            "[ClubName]": studio_name,
            "{{membership_name}}": membership_name,
            "[MembershipName]": membership_name,
            "{{contract_length}}": contract_length,
            "[ContractLength]": contract_length,
            "{{start_date}}": start_date_str,
            "[StartDate]": start_date_str,
            "{{membership_number}}": membership_number,
            "[MembershipNumber]": membership_number,
            "{{payment_schedule}}": payment_schedule,
            "[PaymentSchedule]": payment_schedule,
            "{{manage_online_link}}": manage_online,
            "[ManageOnlineLink]": manage_online,
            "{{welcome_guide_link}}": welcome_guide,
            "[Insert Welcome Guide Link]": welcome_guide,
            "{{healthy_habits_guide_link}}": healthy_habits,
            "[Insert Healthy Habits Guide Link]": healthy_habits,
            "{{testimonials_link}}": testimonials,
            "(Insert link to testimonials page)": testimonials,
            "{{testimonial_video_link}}": testimonial_video,
            "[Testimonial video link]": testimonial_video,
            "{{nutrition_guide_link}}": nutrition_guide,
            "[Insert Nutrition Guide Link]": nutrition_guide,
            "{{referral_link}}": referral_link,
            "(Insert club referral link)": referral_link,
            "{{recipe_guide_link}}": recipe_guide,
            "[Insert Recipe Guide Link]": recipe_guide,
        }

    @classmethod
    def _get_client_join_date(cls, client: User, tenant):
        """
        Determines a client's effective Day 0 join date at the gym:
        earliest purchased package date if available, otherwise client.date_joined.
        """
        first_pkg = (
            Package.all_objects.filter(tenant=tenant, client=client)
            .order_by('purchased_at', 'created_at')
            .first()
        )
        if first_pkg:
            dt = first_pkg.purchased_at or first_pkg.created_at
            if dt:
                return dt.date()
        if client.date_joined:
            return client.date_joined.date()
        return timezone.now().date()

    @classmethod
    def evaluate_onboarding_milestone_triggers(
        cls,
        tenant=None,
        client: Optional[User] = None,
        milestone_day: Optional[int] = None,
    ) -> List[RetentionCampaignActionLog]:
        """
        Evaluates active ONBOARDING_MILESTONE triggers (Day 0, 2, 7, 14, 21, 28, 42, 70).
        Each milestone email is sent at most ONCE in a client's lifetime per trigger.
        """
        today = timezone.now().date()
        logs_created = []

        with bypass_tenant_isolation():
            triggers_qs = RetentionCampaignTrigger.all_objects.filter(
                trigger_type=RetentionTriggerType.ONBOARDING_MILESTONE,
                is_active=True,
            ).select_related('tenant', 'target_segment')

            if tenant:
                triggers_qs = triggers_qs.filter(tenant=tenant)
            if milestone_day is not None:
                triggers_qs = triggers_qs.filter(trigger_value=milestone_day)

            for trigger in triggers_qs:
                settings_obj = TenantOnboardingSettings.all_objects.filter(tenant=trigger.tenant).first()
                if settings_obj and not settings_obj.onboarding_emails_enabled:
                    continue

                target_days = max(0, trigger.trigger_value)

                clients_qs = User.objects.filter(
                    tenant=trigger.tenant,
                    role=UserRole.CLIENT,
                    is_active=True,
                )
                if client is not None:
                    clients_qs = clients_qs.filter(id=client.id)

                for candidate in clients_qs:
                    # Lifetime idempotency check: never send the same onboarding milestone twice
                    already_sent = RetentionCampaignActionLog.all_objects.filter(
                        tenant=trigger.tenant,
                        client=candidate,
                        trigger=trigger,
                    ).exists()
                    if already_sent:
                        continue

                    join_date = cls._get_client_join_date(candidate, trigger.tenant)
                    days_since_join = (today - join_date).days

                    if days_since_join == target_days:
                        metric = ClientRetentionMetrics.all_objects.filter(
                            tenant=trigger.tenant,
                            client=candidate,
                        ).first()
                        log = cls.execute_trigger(client=candidate, trigger=trigger, metrics=metric)
                        if log:
                            logs_created.append(log)

        return logs_created

    @classmethod
    def trigger_day_zero_onboarding_email_for_client(cls, tenant, client: User) -> List[RetentionCampaignActionLog]:
        """
        Ensures default onboarding triggers exist for the tenant and immediately evaluates
        Day 0 onboarding email for a newly joined client or first-time membership purchase.
        """
        if not tenant or not client or client.role != UserRole.CLIENT:
            return []
        cls.seed_default_onboarding_triggers(tenant)
        return cls.evaluate_onboarding_milestone_triggers(
            tenant=tenant,
            client=client,
            milestone_day=0,
        )

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

        tokens = cls._build_onboarding_context_tokens(client, trigger.tenant)
        studio_name = tokens["{{studio_name}}"]

        # Format Subject
        subject = trigger.template_subject or f"Important message from {studio_name}"
        for k, v in tokens.items():
            subject = subject.replace(k, str(v))

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
            for k, v in tokens.items():
                body = body.replace(k, str(v))

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
        logs.extend(cls.evaluate_onboarding_milestone_triggers(tenant=tenant))
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
