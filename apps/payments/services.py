import decimal
from django.db import transaction
from typing import Optional
from .models import PlatformLedger, TenantPayout, PlatformSettings
from apps.core.tenants.models import Tenant

class LedgerService:
    """
    Service layer for handling all payment ledger and payout logic.
    Ensures that financial transactions are atomic and properly isolated.
    """

    @staticmethod
    def calculate_platform_cut(amount_gross: decimal.Decimal) -> decimal.Decimal:
        """
        Calculates the platform fee based on global platform settings.
        """
        settings = PlatformSettings.get_settings()
        fee_percentage = decimal.Decimal(str(settings.platform_fee_percentage))
        
        # Calculate platform fee
        platform_fee = (amount_gross * fee_percentage) / decimal.Decimal(100.0)
        return platform_fee.quantize(decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_UP)

    @staticmethod
    @transaction.atomic
    def record_transaction(
        tenant: Tenant, 
        amount_gross: decimal.Decimal, 
        transaction_id: str, 
        description: str = "",
        payment_instance = None,
        tx_type: str = PlatformLedger.TransactionType.CHARGE
    ) -> PlatformLedger:
        """
        Registers a transaction (e.g., booking payment) into the platform ledger.
        """
        amount_gross = decimal.Decimal(str(amount_gross))
        platform_fee = LedgerService.calculate_platform_cut(amount_gross)
        amount_net = amount_gross - platform_fee

        # If it's a refund, amounts are negated
        if tx_type == PlatformLedger.TransactionType.REFUND:
            amount_gross = -amount_gross
            platform_fee = -platform_fee
            amount_net = -amount_net

        ledger = PlatformLedger.objects.create(
            tenant=tenant,
            transaction_id=transaction_id,
            amount_gross=amount_gross,
            platform_fee=platform_fee,
            amount_net=amount_net,
            description=description,
            type=tx_type,
            status=PlatformLedger.StatusChoices.PENDING
        )

        return ledger

    @staticmethod
    @transaction.atomic
    def process_payout_for_tenant(tenant: Tenant) -> Optional[TenantPayout]:
        """
        Aggregates all unpaid ledger entries for a tenant and creates a payout record.
        """
        unpaid_ledgers = PlatformLedger.objects.filter(
            tenant=tenant,
            status=PlatformLedger.StatusChoices.PENDING
        )

        if not unpaid_ledgers.exists():
            return None

        # Calculate total net amount owed
        total_payout_amount = sum(ledger.amount_net for ledger in unpaid_ledgers)
        
        if total_payout_amount <= 0:
            return None

        # Create the payout record
        payout = TenantPayout.objects.create(
            tenant=tenant,
            amount=total_payout_amount,
            status=TenantPayout.StatusChoices.PENDING
        )

        # Link ledgers to payout and mark as paid (or processing)
        unpaid_ledgers.update(
            status=PlatformLedger.StatusChoices.PAID,
            payout=payout
        )

        return payout
