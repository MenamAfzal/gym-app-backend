"""
Rewards Signals

Handles automatic wallet creation on user registration and lifecycle events.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.users.models import User, UserRole
from apps.rewards.models import RewardWallet
from apps.core.tenants.context import bypass_tenant_isolation


@receiver(post_save, sender=User)
def create_user_reward_wallet(sender, instance, created, **kwargs):
    """
    Ensures every tenant client gets an initialized RewardWallet upon registration.
    """
    if created and instance.tenant_id and instance.role == UserRole.CLIENT:
        with bypass_tenant_isolation():
            RewardWallet.objects.get_or_create(
                tenant_id=instance.tenant_id,
                user=instance,
                defaults={'balance': 0, 'lifetime_earned': 0, 'lifetime_redeemed': 0}
            )
