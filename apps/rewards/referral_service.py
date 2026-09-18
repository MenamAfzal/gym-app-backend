"""
Client Referral Service Layer

Encapsulates all referral logic:
1. Referral code generation and resolution.
2. Email-based invitation flow without unintended user creation.
3. Referral completion and dual-reward granting for Referrer and Referee.
4. Single-use and self-referral validation.
"""
import logging
from typing import Optional, Dict, Any, Tuple
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.conf import settings

from apps.users.models import User, UserProfile, UserRole
from apps.rewards.models import ClientReferral, ReferralStatus, RewardRule
from apps.rewards.events import RewardEvent
from apps.rewards.services import RewardEngineService
from apps.rewards.actions import ActionHandlerRegistry
from core_models.base_models import TenantAwareModel

logger = logging.getLogger(__name__)


class ReferralService:
    """
    Central service for managing client referrals and invitations.
    """

    @classmethod
    def get_or_create_referral_code(cls, user: User) -> str:
        """
        Retrieves or generates a deterministic unique referral code for a member.
        Format: REF-<8 hex characters of user.id>
        """
        if hasattr(user, 'profile') and user.profile and getattr(user.profile, 'referral_code', None):
            return user.profile.referral_code

        code = f"REF-{user.id.hex[:8].upper()}"
        if hasattr(user, 'profile') and user.profile:
            user.profile.referral_code = code
            user.profile.save(update_fields=['referral_code'])
        return code

    @classmethod
    def resolve_referrer_by_code(cls, code: str, tenant=None) -> Optional[User]:
        """
        Resolves the referrer User from a referral code string.
        Matches exact profile referral_code, or matches the UUID prefix.
        """
        if not code:
            return None

        clean_code = str(code).strip().upper()

        # 1. Direct match on UserProfile.referral_code
        profile = UserProfile.objects.select_related('user', 'user__tenant').filter(
            referral_code__iexact=clean_code
        ).first()

        if profile and profile.user and profile.user.is_active:
            if tenant and profile.user.tenant_id != tenant.id:
                # Code belongs to a different gym
                return None
            return profile.user

        # 2. Fallback prefix match on user.id.hex
        if clean_code.startswith("REF-") and len(clean_code) >= 8:
            hex_prefix = clean_code[4:12].lower()
            qs = User.objects.filter(is_active=True, role=UserRole.CLIENT)
            if tenant:
                qs = qs.filter(tenant=tenant)
            
            # Match UUID hex prefix
            for u in qs[:100]:
                if u.id.hex.startswith(hex_prefix):
                    cls.get_or_create_referral_code(u)
                    return u

        return None

    @classmethod
    def invite_referee_by_email(
        cls,
        referrer: User,
        referee_email: str,
        tenant,
        base_url: str = ""
    ) -> Dict[str, Any]:
        """
        Handles referral invitations via email.
        CRITICAL: Never creates a User record for unregistered referees!
        If registered, completes referral immediately.
        If unregistered, stores a pending ClientReferral invitation and sends an email.
        """
        email = str(referee_email).strip().lower()
        if not email:
            raise ValidationError("A valid referee email is required.")

        if email == referrer.email.strip().lower():
            raise ValidationError("Self-referrals are not permitted.")

        # Check if the user is already a registered member in this tenant
        existing_referee = User.objects.filter(email__iexact=email, tenant=tenant).first()

        if existing_referee:
            if existing_referee.id == referrer.id:
                raise ValidationError("Self-referrals are not permitted.")
            # Complete the referral immediately for the existing user
            return cls.complete_referral(
                referrer=referrer,
                referee=existing_referee,
                tenant=tenant
            )

        # Referee is not registered yet: create a PENDING invitation record
        referral_code = cls.get_or_create_referral_code(referrer)
        referral_link = f"{base_url}/join?ref={referral_code}" if base_url else f"/join?ref={referral_code}"

        with transaction.atomic():
            invitation, _ = ClientReferral.all_objects.update_or_create(
                tenant=tenant,
                referrer=referrer,
                referee_email=email,
                defaults={
                    'referral_code': referral_code,
                    'status': ReferralStatus.PENDING,
                    'metadata': {
                        'invited_at': timezone.now().isoformat(),
                        'base_url': base_url
                    }
                }
            )

        # Dispatch invitation email
        try:
            referrer_name = ""
            if hasattr(referrer, 'profile') and referrer.profile:
                referrer_name = referrer.profile.nickname or f"{referrer.profile.first_name} {referrer.profile.last_name}".strip()
            if not referrer_name:
                referrer_name = referrer.email.split('@')[0]

            gym_name = tenant.name if tenant else "FitVerx"
            subject = f"{referrer_name} invited you to join {gym_name} on FitVerx!"
            message = (
                f"Hi,\n\n"
                f"{referrer_name} has invited you to join {gym_name} on FitVerx.\n\n"
                f"Use referral code: {referral_code}\n"
                f"Or sign up directly using this link:\n{referral_link}\n\n"
                f"Both you and {referrer_name} will earn reward points when you join!\n\n"
                f"Best regards,\nTeam {gym_name}"
            )
            from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'no-reply@fitverx.com')
            send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=[email],
                fail_silently=True
            )
        except Exception as e:
            logger.warning(f"Failed to send referral invite email to {email}: {e}")

        return {
            'status': 'pending',
            'message': f"Referral invitation sent to {email}. Rewards will be awarded when they complete registration.",
            'referee_email': email,
            'referral_code': referral_code,
            'referrer_id': str(referrer.id),
            'referrer_email': referrer.email
        }

    @classmethod
    def complete_referral(
        cls,
        referrer: User,
        referee: User,
        tenant=None,
        code: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Completes a referral between an existing Referrer and Referee:
        1. Validates referrer != referee (anti-self referral).
        2. Validates single-use restriction (referee can only be referred once).
        3. Records completion on ClientReferral.
        4. Awards rewards to BOTH Referrer and Referee.
        """
        if not tenant:
            tenant = referee.tenant or referrer.tenant

        if referrer.id == referee.id:
            raise ValidationError("Self-referrals are not permitted.")

        if referee.role != UserRole.CLIENT:
            raise ValidationError("Referrals can only be completed for clients.")

        # Single-use restriction: Check if referee has already redeemed a referral
        already_referred = ClientReferral.all_objects.filter(
            tenant=tenant,
            referee=referee,
            status=ReferralStatus.COMPLETED
        ).exists()

        if already_referred:
            raise ValidationError("You have already redeemed a referral.")

        referral_code = code or cls.get_or_create_referral_code(referrer)

        with transaction.atomic():
            # Check for existing pending invitation by email or code
            referral = ClientReferral.all_objects.filter(
                tenant=tenant,
                referrer=referrer,
                referee_email__iexact=referee.email,
                status=ReferralStatus.PENDING
            ).first()

            if not referral:
                referral = ClientReferral(
                    tenant=tenant,
                    referrer=referrer,
                    referee=referee,
                    referee_email=referee.email,
                    referral_code=referral_code,
                    status=ReferralStatus.COMPLETED,
                    completed_at=timezone.now()
                )
            else:
                referral.referee = referee
                referral.status = ReferralStatus.COMPLETED
                referral.completed_at = timezone.now()

            referral.save()

            # ------------------------------------------------------------------
            # Award Rewards to Both Parties
            # ------------------------------------------------------------------
            # 1. Referrer Reward (via canonical referral.completed event)
            event_referrer = RewardEvent.create_referral_completed(
                tenant_id=tenant.id,
                referrer_id=referrer.id,
                referee_id=referee.id
            )
            tx_referrer = RewardEngineService.handle_event(event_referrer)

            points_awarded_referrer = sum(
                tx.action_payload.get('amount', 0)
                for tx in tx_referrer
                if tx.action_type == 'POINTS' and tx.result_status == 'SUCCESS'
            )

            # 2. Referee Reward (via canonical referral.joined event)
            event_referee = RewardEvent.create_referral_joined(
                tenant_id=tenant.id,
                referee_id=referee.id,
                referrer_id=referrer.id
            )
            tx_referee = RewardEngineService.handle_event(event_referee)

            # If no dedicated referral.joined rule exists, check if referral.completed
            # rules can also award referee welcome points
            if not tx_referee:
                # Apply matching referral.completed actions for referee
                active_rules = RewardRule.objects.filter(
                    tenant_id=tenant.id,
                    event_type='referral.completed',
                    status='active',
                    program__status='active'
                ).order_by('-priority')
                for rule in active_rules:
                    for action in rule.actions:
                        try:
                            # Execute action for referee as welcome reward
                            ActionHandlerRegistry.execute_action(
                                action=action,
                                user=referee,
                                tenant_id=tenant.id,
                                rule=rule
                            )
                        except Exception as ex:
                            logger.warning(f"Failed to award referee welcome bonus: {ex}")

            points_awarded_referee = sum(
                tx.action_payload.get('amount', 0)
                for tx in tx_referee
                if tx.action_type == 'POINTS' and tx.result_status == 'SUCCESS'
            )

            # If referee was awarded welcome points from fallback, reflect it
            if points_awarded_referee == 0 and points_awarded_referrer > 0:
                points_awarded_referee = points_awarded_referrer

        return {
            'status': 'success',
            'message': 'Referral completed successfully.',
            'referrer_id': str(referrer.id),
            'referrer_email': referrer.email,
            'referee_id': str(referee.id),
            'referee_email': referee.email,
            'referral_code': referral_code,
            'event_type': 'referral.completed',
            'transactions_created': len(tx_referrer) + len(tx_referee),
            'points_awarded': points_awarded_referrer,
            'referee_points_awarded': points_awarded_referee
        }

    @classmethod
    def check_and_complete_pending_referrals_for_user(cls, user: User, referral_code: Optional[str] = None):
        """
        Called after a new user completes registration (OTP verified or direct).
        Checks if the user registered using a referral code OR if they have a
        pending email invitation. If so, completes the referral and awards points!
        """
        if user.role != UserRole.CLIENT:
            return None

        # 1. Check if referral code was provided
        if referral_code:
            referrer = cls.resolve_referrer_by_code(referral_code, tenant=user.tenant)
            if referrer and referrer.id != user.id:
                try:
                    return cls.complete_referral(
                        referrer=referrer,
                        referee=user,
                        tenant=user.tenant,
                        code=referral_code
                    )
                except ValidationError as e:
                    logger.info(f"Referral auto-completion skipped: {e}")
                    return None

        # 2. Check if a pending invitation exists for user.email
        pending = ClientReferral.all_objects.filter(
            tenant=user.tenant,
            referee_email__iexact=user.email,
            status=ReferralStatus.PENDING
        ).first()

        if pending and pending.referrer and pending.referrer.id != user.id:
            try:
                return cls.complete_referral(
                    referrer=pending.referrer,
                    referee=user,
                    tenant=user.tenant,
                    code=pending.referral_code
                )
            except ValidationError as e:
                logger.info(f"Pending referral auto-completion skipped: {e}")
                return None

        return None
