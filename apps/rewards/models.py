"""
Rewards & Loyalty Engine Models

Multi-tenant, dynamic, rule-driven rewards, points, badges, tiers, streaks,
and catalog redemption engine.
"""
import uuid
from django.db import models
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from core_models.base_models import TenantAwareModel, BaseModel


class ProgramType(models.TextChoices):
    LOYALTY = 'loyalty', _('Evergreen Loyalty')
    CHALLENGE = 'challenge', _('Time-bound Challenge')
    STREAK = 'streak', _('Streak & Consistency')
    REFERRAL = 'referral', _('Member Referral')
    TIERED_VIP = 'tiered_vip', _('Tiered VIP')


class RuleStatus(models.TextChoices):
    DRAFT = 'draft', _('Draft')
    ACTIVE = 'active', _('Active')
    PAUSED = 'paused', _('Paused')
    ARCHIVED = 'archived', _('Archived')


class TransactionType(models.TextChoices):
    EARN = 'EARN', _('Earn Points')
    REDEEM = 'REDEEM', _('Redeem Points')
    EXPIRE = 'EXPIRE', _('Points Expired')
    ADJUSTMENT = 'ADJUSTMENT', _('Manual Adjustment')
    REVERSAL = 'REVERSAL', _('Transaction Reversal')


class ExecutionStatus(models.TextChoices):
    SUCCESS = 'SUCCESS', _('Success')
    PARTIAL = 'PARTIAL', _('Partial')
    FAILED = 'FAILED', _('Failed')
    REVERSED = 'REVERSED', _('Reversed')


class CatalogItemType(models.TextChoices):
    MERCHANDISE = 'MERCHANDISE', _('Merchandise / Physical Item')
    DISCOUNT_CODE = 'DISCOUNT_CODE', _('Discount Voucher')
    FREE_CLASS = 'FREE_CLASS', _('Free Class Credit')
    PACKAGE_CREDIT = 'PACKAGE_CREDIT', _('Package Credit')
    CUSTOM = 'CUSTOM', _('Custom Reward')


class RedemptionStatus(models.TextChoices):
    PENDING = 'PENDING', _('Pending Fulfillment')
    APPROVED = 'APPROVED', _('Approved')
    FULFILLED = 'FULFILLED', _('Fulfilled')
    CANCELLED = 'CANCELLED', _('Cancelled')
    REVERSED = 'REVERSED', _('Reversed')


class RewardProgram(TenantAwareModel):
    """
    Groups related reward rules, campaigns, or loyalty tiers for a tenant.
    """
    name = models.CharField(max_length=150, help_text="Program name e.g. 'VIP Loyalty', 'Spring Streak'")
    program_type = models.CharField(
        max_length=30,
        choices=ProgramType.choices,
        default=ProgramType.LOYALTY,
        help_text="Program archetype"
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    start_date = models.DateTimeField(null=True, blank=True)
    end_date = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = _('Reward Program')
        verbose_name_plural = _('Reward Programs')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'is_active', 'program_type']),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_program_type_display()})"


class RewardRule(TenantAwareModel):
    """
    Configurable reward rule defining WHEN an event happens, IF conditions match, THEN actions execute.
    """
    program = models.ForeignKey(
        RewardProgram,
        on_delete=models.CASCADE,
        related_name='rules',
        help_text="Parent reward program"
    )
    name = models.CharField(max_length=200, help_text="Human-readable rule title")
    description = models.TextField(blank=True)
    event_type = models.CharField(
        max_length=100,
        db_index=True,
        help_text="Event identifier e.g. 'booking.attended', 'workout.completed'"
    )
    status = models.CharField(
        max_length=20,
        choices=RuleStatus.choices,
        default=RuleStatus.ACTIVE,
        db_index=True
    )
    version = models.PositiveIntegerField(default=1, help_text="Auto-incremented on rule configuration updates")
    
    # Sandboxed configuration DSL
    trigger_config = models.JSONField(
        default=dict,
        blank=True,
        help_text="Event payload filters e.g. {'class_category': 'strength'}"
    )
    conditions = models.JSONField(
        default=list,
        blank=True,
        help_text="List of condition objects for safe sandboxed evaluation"
    )
    actions = models.JSONField(
        default=list,
        help_text="List of action objects executed on condition match"
    )

    # Frequency & Milestone constraints
    max_executions_per_user = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Total lifetime executions allowed per user (null = unlimited)"
    )
    max_executions_per_period = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Maximum executions within rolling window"
    )
    period_window_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Rolling window length in days for execution limit"
    )
    priority = models.IntegerField(default=0, help_text="Higher priority rules evaluate first")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_reward_rules'
    )

    class Meta:
        verbose_name = _('Reward Rule')
        verbose_name_plural = _('Reward Rules')
        ordering = ['-priority', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'event_type', 'status']),
            models.Index(fields=['tenant', 'program', 'status']),
        ]

    def __str__(self):
        return f"{self.name} [v{self.version}] ({self.status})"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        
        # Always maintain an immutable snapshot in RewardRuleVersion
        if is_new or not self.versions.filter(version=self.version).exists():
            RewardRuleVersion.objects.create(
                tenant=self.tenant,
                rule=self,
                version=self.version,
                trigger_config_snapshot=self.trigger_config,
                conditions_snapshot=self.conditions,
                actions_snapshot=self.actions,
                created_by=self.created_by,
                change_summary=f"Version {self.version} snapshot"
            )


class RewardRuleVersion(TenantAwareModel):
    """
    Immutable historical audit snapshot of a RewardRule's configuration at a specific version.
    """
    rule = models.ForeignKey(
        RewardRule,
        on_delete=models.CASCADE,
        related_name='versions'
    )
    version = models.PositiveIntegerField()
    trigger_config_snapshot = models.JSONField(default=dict)
    conditions_snapshot = models.JSONField(default=list)
    actions_snapshot = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reward_rule_version_snapshots'
    )
    change_summary = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Reward Rule Version')
        verbose_name_plural = _('Reward Rule Versions')
        unique_together = ('rule', 'version')
        ordering = ['rule', '-version']

    def __str__(self):
        return f"{self.rule.name} (v{self.version})"


class Badge(TenantAwareModel):
    """
    Represents an achievement badge that can be awarded to members.
    """
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=150)
    description = models.TextField(blank=True)
    icon_url = models.URLField(max_length=500, blank=True, null=True)
    category = models.CharField(max_length=50, default='general')
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Badge')
        verbose_name_plural = _('Badges')
        unique_together = ('tenant', 'slug')
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.tenant.name if self.tenant else 'No Tenant'})"


class RewardTier(TenantAwareModel):
    """
    VIP / Loyalty Tier based on lifetime points earned.
    """
    program = models.ForeignKey(
        RewardProgram,
        on_delete=models.CASCADE,
        related_name='tiers'
    )
    name = models.CharField(max_length=100, help_text="e.g. Bronze, Silver, Gold, Platinum")
    threshold_points = models.PositiveIntegerField(default=0, help_text="Lifetime points needed to reach this tier")
    multiplier = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=1.00,
        help_text="Point multiplier (e.g. 1.25 for 25% bonus)"
    )
    perks_description = models.TextField(blank=True)
    badge = models.ForeignKey(
        Badge,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='tier_associations'
    )

    class Meta:
        verbose_name = _('Reward Tier')
        verbose_name_plural = _('Reward Tiers')
        ordering = ['threshold_points']
        unique_together = ('program', 'name')

    def __str__(self):
        return f"{self.name} ({self.threshold_points}+ pts)"


class RewardWallet(TenantAwareModel):
    """
    Per-member, per-tenant loyalty points wallet.
    Maintains current spendable balance, lifetime statistics, and current VIP tier.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reward_wallets'
    )
    balance = models.IntegerField(default=0, help_text="Current spendable points balance")
    lifetime_earned = models.IntegerField(default=0, help_text="Total points earned all-time")
    lifetime_redeemed = models.IntegerField(default=0, help_text="Total points redeemed all-time")
    current_tier = models.ForeignKey(
        RewardTier,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='tier_members'
    )

    class Meta:
        verbose_name = _('Reward Wallet')
        verbose_name_plural = _('Reward Wallets')
        unique_together = ('tenant', 'user')
        indexes = [
            models.Index(fields=['tenant', 'user']),
            models.Index(fields=['tenant', 'balance']),
        ]

    def __str__(self):
        return f"Wallet: {self.user.email} - Balance: {self.balance} pts"


class UserBadge(TenantAwareModel):
    """
    Records an earned badge instance for a member.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='earned_badges'
    )
    badge = models.ForeignKey(
        Badge,
        on_delete=models.CASCADE,
        related_name='awarded_users'
    )
    earned_at = models.DateTimeField(auto_now_add=True)
    source_rule = models.ForeignKey(
        RewardRule,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='awarded_badges'
    )

    class Meta:
        verbose_name = _('User Badge')
        verbose_name_plural = _('User Badges')
        unique_together = ('tenant', 'user', 'badge')
        ordering = ['-earned_at']

    def __str__(self):
        return f"{self.user.email} earned {self.badge.name}"


class UserStreak(TenantAwareModel):
    """
    Tracks consecutive active periods (days/weeks) for streaks (attendance, logging, workouts).
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='activity_streaks'
    )
    activity_type = models.CharField(max_length=50, help_text="e.g. 'attendance', 'workout', 'reflection'")
    current_streak = models.PositiveIntegerField(default=0)
    longest_streak = models.PositiveIntegerField(default=0)
    last_activity_date = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = _('User Streak')
        verbose_name_plural = _('User Streaks')
        unique_together = ('tenant', 'user', 'activity_type')

    def __str__(self):
        return f"{self.user.email} - {self.activity_type}: {self.current_streak} streak"


class ProcessedRewardEvent(TenantAwareModel):
    """
    Deduplication log for canonical business events ingested into the reward engine.
    Ensures strict idempotency across network retries, queues, and duplicate requests.
    """
    event_type = models.CharField(max_length=100)
    idempotency_key = models.CharField(max_length=255, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='processed_reward_events'
    )
    occurred_at = models.DateTimeField()
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=30, default='PROCESSED')

    class Meta:
        verbose_name = _('Processed Reward Event')
        verbose_name_plural = _('Processed Reward Events')
        unique_together = ('tenant', 'event_type', 'idempotency_key')
        indexes = [
            models.Index(fields=['tenant', 'event_type', 'idempotency_key']),
        ]

    def __str__(self):
        return f"[{self.event_type}] {self.idempotency_key} ({self.status})"


class RewardTransaction(TenantAwareModel):
    """
    Immutable ledger of every reward action successfully or partially executed.
    Stores exact snapshots of the rule configuration, action payload, and result data.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reward_transactions'
    )
    rule = models.ForeignKey(
        RewardRule,
        on_delete=models.PROTECT,
        related_name='transactions'
    )
    rule_version = models.PositiveIntegerField(default=1)
    rule_config_snapshot = models.JSONField(help_text="Immutable snapshot of the rule configuration at execution time")
    event_record = models.ForeignKey(
        ProcessedRewardEvent,
        on_delete=models.CASCADE,
        related_name='transactions'
    )
    action_type = models.CharField(max_length=50)
    action_payload = models.JSONField()
    result_status = models.CharField(
        max_length=20,
        choices=ExecutionStatus.choices,
        default=ExecutionStatus.SUCCESS
    )
    result_data = models.JSONField(default=dict)
    milestone_key = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True,
        help_text="Deduplication key for milestone achievements e.g. 'milestone:attendance:10'"
    )

    class Meta:
        verbose_name = _('Reward Transaction')
        verbose_name_plural = _('Reward Transactions')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'user', 'created_at']),
            models.Index(fields=['tenant', 'rule', 'created_at']),
            models.Index(fields=['tenant', 'rule', 'milestone_key']),
        ]

    def __str__(self):
        return f"{self.action_type} for {self.user.email} from {self.rule.name} ({self.result_status})"


class RewardCatalogItem(TenantAwareModel):
    """
    Items in the tenant's reward store available for points redemption.
    """
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    points_cost = models.PositiveIntegerField(help_text="Points required to redeem")
    item_type = models.CharField(
        max_length=30,
        choices=CatalogItemType.choices,
        default=CatalogItemType.MERCHANDISE
    )
    stock_quantity = models.IntegerField(
        null=True,
        blank=True,
        help_text="Available inventory count (null for unlimited)"
    )
    is_active = models.BooleanField(default=True, db_index=True)
    image_url = models.URLField(max_length=500, blank=True, null=True)
    
    # Optional direct integration with packages
    package_type = models.ForeignKey(
        'scheduling.PackageType',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reward_catalog_items',
        help_text="Optional link to PackageType for FREE_CLASS or PACKAGE_CREDIT redemptions"
    )

    class Meta:
        verbose_name = _('Reward Catalog Item')
        verbose_name_plural = _('Reward Catalog Items')
        ordering = ['points_cost', 'name']
        indexes = [
            models.Index(fields=['tenant', 'is_active']),
        ]

    def __str__(self):
        return f"{self.name} ({self.points_cost} pts)"


class RewardRedemption(TenantAwareModel):
    """
    Record of a member spending points to redeem an item from the reward catalog.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reward_redemptions'
    )
    catalog_item = models.ForeignKey(
        RewardCatalogItem,
        on_delete=models.PROTECT,
        related_name='redemptions'
    )
    points_spent = models.PositiveIntegerField()
    status = models.CharField(
        max_length=20,
        choices=RedemptionStatus.choices,
        default=RedemptionStatus.PENDING,
        db_index=True
    )
    redemption_code = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique voucher code shown to member and scanned/validated by front desk staff"
    )
    fulfilled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='fulfilled_reward_redemptions',
        help_text="Staff user who marked the reward fulfilled"
    )
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Reward Redemption')
        verbose_name_plural = _('Reward Redemptions')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'status', 'created_at']),
            models.Index(fields=['tenant', 'user', 'status']),
        ]

    def __str__(self):
        return f"Redemption {self.redemption_code} by {self.user.email}: {self.catalog_item.name} ({self.status})"


class RewardPointLedger(TenantAwareModel):
    """
    Detailed audit line-item for every debit and credit against a RewardWallet.
    """
    wallet = models.ForeignKey(
        RewardWallet,
        on_delete=models.CASCADE,
        related_name='ledger_entries'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reward_point_ledger_entries'
    )
    amount = models.IntegerField(help_text="Positive for credit/earn, negative for debit/redeem")
    balance_after = models.IntegerField(help_text="Wallet balance snapshot immediately following this transaction")
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
        default=TransactionType.EARN
    )
    description = models.CharField(max_length=255)
    source_transaction = models.ForeignKey(
        RewardTransaction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='ledger_entries'
    )
    redemption = models.ForeignKey(
        RewardRedemption,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='ledger_entries'
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    is_expired = models.BooleanField(default=False)

    class Meta:
        verbose_name = _('Reward Point Ledger Entry')
        verbose_name_plural = _('Reward Point Ledger Entries')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'user', 'created_at']),
            models.Index(fields=['wallet', 'created_at']),
        ]

    def __str__(self):
        sign = "+" if self.amount >= 0 else ""
        return f"{self.user.email}: {sign}{self.amount} pts ({self.transaction_type}) -> Bal: {self.balance_after}"
