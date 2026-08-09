"""
Audience Resolver

Resolves a NotificationCampaign's audience_type to a concrete User queryset.
All resolutions are strictly tenant-scoped — no cross-tenant data is ever returned.

Supported audience types:
    ALL_CLIENTS          → all client-role users in this tenant
    ALL_STAFF            → all trainer/manager/front_desk users
    ALL_TRAINERS         → all trainer-role users
    ALL_MANAGERS         → all gym_manager-role users
    SPECIFIC_USERS       → campaign.audience_users (validated to same tenant)
    GROUP                → members of campaign.audience_group
    CLASS_BOOKINGS       → clients booked into a specific session
    CLASS_WAITLIST       → clients on a specific session's waitlist
    TRAINER_CLIENTS      → clients assigned to a specific trainer
    APPOINTMENT_ATTENDEES → client of a specific appointment
    DYNAMIC_FILTER       → predefined filter set (no arbitrary query generation)

DynamicAudienceFilter supports:
    membership_expiring_within_days  → packages expiring within N days
    assigned_trainer                 → clients assigned to a specific trainer
    booked_today                     → clients with bookings today
    active_members                   → clients with active packages
    last_login_older_than_days       → clients who haven't logged in for N+ days
"""
import logging
from datetime import timedelta
from django.utils import timezone
from django.db.models import QuerySet

from apps.users.models import User, UserRole

logger = logging.getLogger(__name__)


class AudienceResolver:
    """
    Resolves a campaign's audience_type to a User queryset.
    All queries are scoped to the campaign's tenant via TenantAwareManager.
    """

    @staticmethod
    def resolve(campaign) -> QuerySet:
        """
        Returns a User queryset for the given campaign's audience configuration.
        The queryset is tenant-scoped (TenantAwareManager applies automatically).

        Args:
            campaign: NotificationCampaign instance (tenant context must be active)

        Returns:
            QuerySet[User] — may be empty but never cross-tenant
        """
        audience_type = campaign.audience_type
        tenant = campaign.tenant

        try:
            return AudienceResolver._resolve(campaign, audience_type, tenant)
        except Exception as e:
            logger.error(f"AudienceResolver failed for campaign {campaign.id}: {e}")
            return User.objects.none()

    @staticmethod
    def _resolve(campaign, audience_type, tenant) -> QuerySet:
        from apps.notifications.models import NotificationAudienceType

        if audience_type == NotificationAudienceType.ALL_CLIENTS:
            return User.objects.filter(tenant=tenant, role=UserRole.CLIENT, is_active=True)

        elif audience_type == NotificationAudienceType.ALL_STAFF:
            return User.objects.filter(
                tenant=tenant,
                role__in=[UserRole.TRAINER, UserRole.GYM_MANAGER, UserRole.FRONT_DESK],
                is_active=True,
            )

        elif audience_type == NotificationAudienceType.ALL_TRAINERS:
            return User.objects.filter(tenant=tenant, role=UserRole.TRAINER, is_active=True)

        elif audience_type == NotificationAudienceType.ALL_MANAGERS:
            return User.objects.filter(tenant=tenant, role=UserRole.GYM_MANAGER, is_active=True)

        elif audience_type == NotificationAudienceType.SPECIFIC_USERS:
            # Validate all selected users belong to this tenant
            return campaign.audience_users.filter(tenant=tenant, is_active=True)

        elif audience_type == NotificationAudienceType.GROUP:
            if not campaign.audience_group:
                logger.warning(f"Campaign {campaign.id} has GROUP audience but no audience_group set.")
                return User.objects.none()
            from apps.notifications.models import NotificationGroupMember
            user_ids = NotificationGroupMember.objects.filter(
                group=campaign.audience_group,
                group__tenant=tenant,
            ).values_list('user_id', flat=True)
            return User.objects.filter(id__in=user_ids, tenant=tenant, is_active=True)

        elif audience_type == NotificationAudienceType.CLASS_BOOKINGS:
            if not campaign.audience_entity_id:
                return User.objects.none()
            from apps.scheduling.models import Booking
            client_ids = Booking.all_objects.filter(
                session_id=campaign.audience_entity_id,
                tenant=tenant,
                status='booked',
            ).values_list('client_id', flat=True)
            return User.objects.filter(id__in=client_ids, is_active=True)

        elif audience_type == NotificationAudienceType.CLASS_WAITLIST:
            if not campaign.audience_entity_id:
                return User.objects.none()
            from apps.scheduling.models import Waitlist
            client_ids = Waitlist.all_objects.filter(
                session_id=campaign.audience_entity_id,
                tenant=tenant,
                status='waiting',
            ).values_list('client_id', flat=True)
            return User.objects.filter(id__in=client_ids, is_active=True)

        elif audience_type == NotificationAudienceType.TRAINER_CLIENTS:
            if not campaign.audience_entity_id:
                return User.objects.none()
            from apps.scheduling.models import StaffClientAssignment
            client_ids = StaffClientAssignment.all_objects.filter(
                staff_id=campaign.audience_entity_id,
                tenant=tenant,
            ).values_list('client_id', flat=True)
            return User.objects.filter(id__in=client_ids, is_active=True)

        elif audience_type == NotificationAudienceType.APPOINTMENT_ATTENDEES:
            if not campaign.audience_entity_id:
                return User.objects.none()
            from apps.scheduling.models import Appointment
            try:
                appointment = Appointment.all_objects.get(
                    id=campaign.audience_entity_id,
                    tenant=tenant,
                )
                return User.objects.filter(id=appointment.client_id, is_active=True)
            except Appointment.DoesNotExist:
                return User.objects.none()

        elif audience_type == NotificationAudienceType.DYNAMIC_FILTER:
            return DynamicAudienceFilter.resolve(tenant, campaign.audience_filter)

        logger.warning(f"Unhandled audience_type '{audience_type}' for campaign {campaign.id}")
        return User.objects.none()


class DynamicAudienceFilter:
    """
    Resolves a predefined filter config to a User queryset.

    Supported filter keys and their value types:
        membership_expiring_within_days: int  — packages expiring in N days
        assigned_trainer: str (UUID)          — clients of trainer X
        booked_today: bool                    — clients with bookings today
        active_members: bool                  — clients with active packages
        last_login_older_than_days: int       — clients with last_login > N days ago

    Filter config example:
        {"membership_expiring_within_days": 7}
        {"booked_today": true}

    Multiple filters are ANDed together.
    Unknown filter keys are silently ignored (logged as warning).
    """

    SUPPORTED_FILTERS = {
        'membership_expiring_within_days',
        'assigned_trainer',
        'booked_today',
        'active_members',
        'last_login_older_than_days',
    }

    @staticmethod
    def resolve(tenant, filter_config: dict) -> QuerySet:
        """
        Resolve filter config to a User queryset.
        All results are scoped to the given tenant.
        """
        now = timezone.now()
        qs = User.objects.filter(tenant=tenant, role=UserRole.CLIENT, is_active=True)

        if not filter_config:
            return qs

        for key, value in filter_config.items():
            if key not in DynamicAudienceFilter.SUPPORTED_FILTERS:
                logger.warning(f"DynamicAudienceFilter: unsupported filter key '{key}' — skipped")
                continue

            if key == 'membership_expiring_within_days':
                try:
                    days = int(value)
                    from apps.scheduling.models import Package
                    expiring_client_ids = Package.all_objects.filter(
                        tenant=tenant,
                        expires_at__date__lte=(now + timedelta(days=days)).date(),
                        expires_at__gte=now,
                        credits_remaining__gt=0,
                    ).values_list('client_id', flat=True)
                    qs = qs.filter(id__in=expiring_client_ids)
                except (ValueError, TypeError):
                    logger.warning(f"DynamicAudienceFilter: invalid value for 'membership_expiring_within_days': {value}")

            elif key == 'assigned_trainer':
                try:
                    from apps.scheduling.models import StaffClientAssignment
                    client_ids = StaffClientAssignment.all_objects.filter(
                        tenant=tenant,
                        staff_id=value,
                    ).values_list('client_id', flat=True)
                    qs = qs.filter(id__in=client_ids)
                except Exception as e:
                    logger.warning(f"DynamicAudienceFilter: 'assigned_trainer' filter failed: {e}")

            elif key == 'booked_today':
                if value:
                    from apps.scheduling.models import Booking
                    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    today_end   = today_start + timedelta(days=1)
                    client_ids = Booking.all_objects.filter(
                        tenant=tenant,
                        session__start_at__gte=today_start,
                        session__start_at__lt=today_end,
                        status='booked',
                    ).values_list('client_id', flat=True)
                    qs = qs.filter(id__in=client_ids)

            elif key == 'active_members':
                if value:
                    from apps.scheduling.models import Package
                    active_client_ids = Package.all_objects.filter(
                        tenant=tenant,
                        expires_at__gte=now,
                        credits_remaining__gt=0,
                    ).values_list('client_id', flat=True)
                    qs = qs.filter(id__in=active_client_ids)

            elif key == 'last_login_older_than_days':
                try:
                    days = int(value)
                    cutoff = now - timedelta(days=days)
                    qs = qs.filter(last_login__lt=cutoff)
                except (ValueError, TypeError):
                    logger.warning(f"DynamicAudienceFilter: invalid value for 'last_login_older_than_days': {value}")

        return qs.distinct()
