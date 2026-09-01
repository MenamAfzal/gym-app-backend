"""
Django Admin Registrations for Rewards
"""
from django.contrib import admin
from apps.rewards.models import (
    RewardProgram, RewardRule, RewardRuleVersion, Badge, RewardTier,
    RewardWallet, RewardPointLedger, UserBadge, UserStreak,
    ProcessedRewardEvent, RewardTransaction, RewardCatalogItem, RewardRedemption
)


@admin.register(RewardProgram)
class RewardProgramAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'program_type', 'is_active', 'created_at')
    list_filter = ('tenant', 'program_type', 'is_active')
    search_fields = ('name', 'tenant__name')
    list_select_related = ('tenant',)


@admin.register(RewardRule)
class RewardRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'event_type', 'status', 'version', 'priority', 'created_at')
    list_filter = ('tenant', 'event_type', 'status')
    search_fields = ('name', 'tenant__name', 'event_type')
    list_select_related = ('tenant', 'program', 'created_by')


@admin.register(RewardRuleVersion)
class RewardRuleVersionAdmin(admin.ModelAdmin):
    list_display = ('rule', 'tenant', 'version', 'created_at')
    list_filter = ('tenant', 'version')
    list_select_related = ('rule', 'tenant', 'created_by')


@admin.register(Badge)
class BadgeAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'slug', 'category', 'is_active')
    list_filter = ('tenant', 'category', 'is_active')
    search_fields = ('name', 'slug', 'tenant__name')
    list_select_related = ('tenant',)


@admin.register(RewardTier)
class RewardTierAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'program', 'threshold_points', 'multiplier')
    list_filter = ('tenant', 'program')
    list_select_related = ('tenant', 'program', 'badge')


@admin.register(RewardWallet)
class RewardWalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'tenant', 'balance', 'lifetime_earned', 'current_tier', 'updated_at')
    list_filter = ('tenant',)
    search_fields = ('user__email', 'tenant__name')
    list_select_related = ('tenant', 'user', 'current_tier')


@admin.register(RewardPointLedger)
class RewardPointLedgerAdmin(admin.ModelAdmin):
    list_display = ('user', 'tenant', 'amount', 'balance_after', 'transaction_type', 'description', 'created_at')
    list_filter = ('tenant', 'transaction_type')
    search_fields = ('user__email', 'description')
    list_select_related = ('tenant', 'user', 'wallet')


@admin.register(UserBadge)
class UserBadgeAdmin(admin.ModelAdmin):
    list_display = ('user', 'tenant', 'badge', 'earned_at')
    list_filter = ('tenant', 'badge')
    search_fields = ('user__email', 'badge__name')
    list_select_related = ('tenant', 'user', 'badge')


@admin.register(UserStreak)
class UserStreakAdmin(admin.ModelAdmin):
    list_display = ('user', 'tenant', 'activity_type', 'current_streak', 'longest_streak', 'last_activity_date')
    list_filter = ('tenant', 'activity_type')
    search_fields = ('user__email',)
    list_select_related = ('tenant', 'user')


@admin.register(ProcessedRewardEvent)
class ProcessedRewardEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'tenant', 'user', 'idempotency_key', 'status', 'occurred_at')
    list_filter = ('tenant', 'event_type', 'status')
    search_fields = ('idempotency_key', 'user__email')
    list_select_related = ('tenant', 'user')


@admin.register(RewardTransaction)
class RewardTransactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'tenant', 'rule', 'action_type', 'result_status', 'created_at')
    list_filter = ('tenant', 'action_type', 'result_status')
    search_fields = ('user__email', 'rule__name')
    list_select_related = ('tenant', 'user', 'rule', 'event_record')


@admin.register(RewardCatalogItem)
class RewardCatalogItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'points_cost', 'item_type', 'stock_quantity', 'is_active')
    list_filter = ('tenant', 'item_type', 'is_active')
    search_fields = ('name', 'tenant__name')
    list_select_related = ('tenant', 'package_type')


@admin.register(RewardRedemption)
class RewardRedemptionAdmin(admin.ModelAdmin):
    list_display = ('redemption_code', 'tenant', 'user', 'catalog_item', 'points_spent', 'status', 'created_at')
    list_filter = ('tenant', 'status')
    search_fields = ('redemption_code', 'user__email', 'catalog_item__name')
    list_select_related = ('tenant', 'user', 'catalog_item', 'fulfilled_by')
