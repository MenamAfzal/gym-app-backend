"""
Affiliate Service Layer

Handles external affiliate partner onboarding, code resolution, tenant linkage,
and recurring point distribution on package subscriptions and renewals.
"""
import logging
import re
import uuid
from typing import Optional, Dict, Any, Tuple
from django.db import transaction
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.users.models import User, UserProfile, UserRole
from apps.core.tenants.models import (
    Tenant, TenantSubscription, AffiliatePartner, AffiliatePointsTransaction
)

logger = logging.getLogger(__name__)

DEFAULT_INITIAL_POINTS = getattr(settings, 'AFFILIATE_INITIAL_SUBSCRIPTION_POINTS', 500)
DEFAULT_RENEWAL_POINTS = getattr(settings, 'AFFILIATE_RENEWAL_SUBSCRIPTION_POINTS', 100)


class AffiliateService:
    """
    Core service managing external B2B affiliates and points attribution.
    """

    @classmethod
    def sanitize_code(cls, raw_code: str) -> str:
        """
        Normalizes and validates a referral code/slug.
        Allows alphanumeric, dashes, underscores.
        """
        if not raw_code:
            return ""
        code = str(raw_code).strip()
        if not re.match(r'^[a-zA-Z0-9_\-]+$', code):
            raise ValidationError(_("Affiliate code can only contain letters, numbers, hyphens, and underscores."))
        return code

    @classmethod
    def generate_unique_code(cls, user: User, custom_code: Optional[str] = None) -> str:
        """
        Generates or validates a unique affiliate code.
        """
        if custom_code:
            code = cls.sanitize_code(custom_code)
            if AffiliatePartner.objects.filter(code__iexact=code).exists():
                raise ValidationError(_(f"Affiliate code '{code}' is already taken. Please choose another."))
            return code

        # Generate default code e.g. AFF-A1B2C3D4
        base_hex = user.id.hex[:8].upper()
        candidate = f"AFF-{base_hex}"
        counter = 1
        while AffiliatePartner.objects.filter(code__iexact=candidate).exists():
            candidate = f"AFF-{base_hex}-{counter}"
            counter += 1
        return candidate

    @classmethod
    @transaction.atomic
    def register_affiliate(
        cls,
        email: str,
        password: str,
        first_name: str = "",
        last_name: str = "",
        code: Optional[str] = None,
        company_name: str = "",
        website_or_social: str = "",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Tuple[User, AffiliatePartner]:
        """
        Registers an external affiliate partner.
        Creates User with role=AFFILIATE (tenant=None) and AffiliatePartner profile.
        """
        clean_email = str(email).strip().lower()
        if not clean_email:
            raise ValidationError(_("Email address is required."))

        if User.objects.filter(email=clean_email).exists():
            raise ValidationError(_("An account with this email already exists."))

        # Create external affiliate user (no tenant context)
        user = User.objects.create_user(
            email=clean_email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=UserRole.AFFILIATE,
            tenant=None,
            is_active=True
        )

        # Create basic UserProfile for consistency
        UserProfile.objects.create(
            user=user,
            nickname=first_name or clean_email.split('@')[0],
            first_name=first_name,
            last_name=last_name
        )

        affiliate_code = cls.generate_unique_code(user, custom_code=code)

        partner = AffiliatePartner.objects.create(
            user=user,
            code=affiliate_code,
            company_name=company_name,
            website_or_social=website_or_social,
            points_balance=0,
            lifetime_points_earned=0,
            is_active=True,
            metadata=metadata or {}
        )

        logger.info(f"Registered external AffiliatePartner {partner.code} for user {user.email}")
        return user, partner

    @classmethod
    def resolve_affiliate(cls, code: str) -> Optional[AffiliatePartner]:
        """
        Resolves an active AffiliatePartner by referral code (case-insensitive).
        """
        if not code:
            return None
        clean_code = str(code).strip()
        return AffiliatePartner.objects.select_related('user').filter(
            code__iexact=clean_code,
            is_active=True
        ).first()

    @classmethod
    def link_tenant_to_affiliate(cls, tenant: Tenant, affiliate_code_or_instance) -> bool:
        """
        Links an existing or newly created Tenant to an external affiliate.
        """
        if not tenant:
            return False

        if isinstance(affiliate_code_or_instance, AffiliatePartner):
            partner = affiliate_code_or_instance
        elif isinstance(affiliate_code_or_instance, str):
            partner = cls.resolve_affiliate(affiliate_code_or_instance)
        else:
            return False

        if not partner:
            return False

        tenant.affiliate = partner
        tenant.save(update_fields=['affiliate'])
        logger.info(f"Linked Tenant '{tenant.name}' to Affiliate '{partner.code}'")
        return True

    @classmethod
    @transaction.atomic
    def award_initial_subscription_points(
        cls,
        tenant: Tenant,
        subscription: Optional[TenantSubscription] = None,
        points: Optional[int] = None
    ) -> Optional[AffiliatePointsTransaction]:
        """
        Awards points to an affiliate when the referred gym first subscribes to a paid package.
        Guards against duplicate initial point grants for the same tenant.
        """
        partner = getattr(tenant, 'affiliate', None)
        if not partner or not partner.is_active:
            return None

        # Guard: Check if initial points were already awarded for this tenant
        already_awarded = AffiliatePointsTransaction.objects.filter(
            affiliate=partner,
            tenant=tenant,
            transaction_type=AffiliatePointsTransaction.TransactionType.INITIAL_SUBSCRIPTION
        ).exists()

        if already_awarded:
            logger.info(f"Initial subscription points already awarded to {partner.code} for tenant {tenant.id}")
            return None

        points_to_award = points if points is not None else DEFAULT_INITIAL_POINTS
        if points_to_award <= 0:
            return None

        # Lock partner row for balance update
        locked_partner = AffiliatePartner.objects.select_for_update().get(id=partner.id)
        locked_partner.points_balance += points_to_award
        locked_partner.lifetime_points_earned += points_to_award
        locked_partner.save(update_fields=['points_balance', 'lifetime_points_earned'])

        tx = AffiliatePointsTransaction.objects.create(
            affiliate=locked_partner,
            tenant=tenant,
            subscription=subscription,
            points=points_to_award,
            transaction_type=AffiliatePointsTransaction.TransactionType.INITIAL_SUBSCRIPTION,
            description=f"Initial subscription for {tenant.name}",
            metadata={
                'gym_name': tenant.name,
                'subdomain': tenant.subdomain,
                'plan_name': subscription.plan.name if subscription and subscription.plan else None
            }
        )

        logger.info(f"Awarded {points_to_award} initial points to Affiliate {locked_partner.code} for Tenant {tenant.name}")
        return tx

    @classmethod
    @transaction.atomic
    def award_renewal_subscription_points(
        cls,
        tenant: Tenant,
        subscription: Optional[TenantSubscription] = None,
        points: Optional[int] = None,
        invoice_id: Optional[str] = None
    ) -> Optional[AffiliatePointsTransaction]:
        """
        Awards recurring renewal points to an affiliate every time the referred gym's package renews.
        Deduplicates against invoice_id if provided.
        """
        partner = getattr(tenant, 'affiliate', None)
        if not partner or not partner.is_active:
            return None

        # Deduplication against invoice_id
        if invoice_id:
            already_processed = AffiliatePointsTransaction.objects.filter(
                affiliate=partner,
                tenant=tenant,
                metadata__invoice_id=invoice_id
            ).exists()
            if already_processed:
                logger.info(f"Renewal points already processed for invoice {invoice_id}")
                return None

        points_to_award = points if points is not None else DEFAULT_RENEWAL_POINTS
        if points_to_award <= 0:
            return None

        # Lock partner row for balance update
        locked_partner = AffiliatePartner.objects.select_for_update().get(id=partner.id)
        locked_partner.points_balance += points_to_award
        locked_partner.lifetime_points_earned += points_to_award
        locked_partner.save(update_fields=['points_balance', 'lifetime_points_earned'])

        metadata = {
            'gym_name': tenant.name,
            'subdomain': tenant.subdomain,
            'plan_name': subscription.plan.name if subscription and subscription.plan else None
        }
        if invoice_id:
            metadata['invoice_id'] = invoice_id

        tx = AffiliatePointsTransaction.objects.create(
            affiliate=locked_partner,
            tenant=tenant,
            subscription=subscription,
            points=points_to_award,
            transaction_type=AffiliatePointsTransaction.TransactionType.SUBSCRIPTION_RENEWAL,
            description=f"Package renewal for {tenant.name}",
            metadata=metadata
        )

        logger.info(f"Awarded {points_to_award} renewal points to Affiliate {locked_partner.code} for Tenant {tenant.name}")
        return tx

    @classmethod
    @transaction.atomic
    def adjust_affiliate_points(
        cls,
        affiliate: AffiliatePartner,
        points: int,
        reason: str,
        admin_user: Optional[User] = None
    ) -> AffiliatePointsTransaction:
        """
        Allows platform admins to manually adjust points (credit or debit).
        """
        if points == 0:
            raise ValidationError(_("Points adjustment must be non-zero."))

        locked_partner = AffiliatePartner.objects.select_for_update().get(id=affiliate.id)

        new_balance = locked_partner.points_balance + points
        if new_balance < 0:
            raise ValidationError(_(f"Cannot adjust points. Current balance is {locked_partner.points_balance}, deduction is {abs(points)}."))

        locked_partner.points_balance = new_balance
        if points > 0:
            locked_partner.lifetime_points_earned += points
        locked_partner.save(update_fields=['points_balance', 'lifetime_points_earned'])

        tx = AffiliatePointsTransaction.objects.create(
            affiliate=locked_partner,
            points=points,
            transaction_type=AffiliatePointsTransaction.TransactionType.MANUAL_ADJUSTMENT,
            description=reason or "Manual administrative adjustment",
            metadata={
                'adjusted_by': str(admin_user.id) if admin_user else None,
                'admin_email': admin_user.email if admin_user else None
            }
        )

        logger.info(f"Admin adjusted {points} points for affiliate {locked_partner.code}. New balance: {locked_partner.points_balance}")
        return tx

    @classmethod
    def get_affiliate_dashboard_data(cls, affiliate: AffiliatePartner, base_url: str = "") -> Dict[str, Any]:
        """
        Aggregates dashboard data for an affiliate partner.
        """
        tenants_qs = Tenant.objects.filter(affiliate=affiliate)
        total_gyms = tenants_qs.count()
        active_gyms = tenants_qs.filter(is_active=True).count()

        recent_txs = affiliate.point_transactions.all()[:10]

        link_base = base_url.rstrip('/') if base_url else ""
        referral_link = f"{link_base}/join-gym?ref={affiliate.code}" if link_base else f"/join-gym?ref={affiliate.code}"

        return {
            'affiliate_id': str(affiliate.id),
            'email': affiliate.user.email,
            'code': affiliate.code,
            'referral_link': referral_link,
            'company_name': affiliate.company_name,
            'points_balance': affiliate.points_balance,
            'lifetime_points_earned': affiliate.lifetime_points_earned,
            'total_gyms_referred': total_gyms,
            'active_gyms_referred': active_gyms,
            'recent_transactions': [
                {
                    'id': str(t.id),
                    'points': t.points,
                    'transaction_type': t.transaction_type,
                    'description': t.description,
                    'gym_name': t.tenant.name if t.tenant else t.metadata.get('gym_name', 'Unknown Gym'),
                    'created_at': t.created_at.isoformat()
                }
                for t in recent_txs
            ]
        }
