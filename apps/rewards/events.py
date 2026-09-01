"""
Canonical Reward Domain Events

Typed, immutable data structures representing business actions emitted
from platform apps (scheduling, workout, payments, nutrition, users)
into the rewards engine.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from uuid import UUID


@dataclass(frozen=True)
class RewardEvent:
    """
    Canonical platform event contract for the Reward Engine.
    """
    tenant_id: UUID
    event_type: str
    user_id: UUID
    idempotency_key: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create_booking_attended(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        booking_id: UUID,
        session_id: Optional[UUID] = None,
        class_name: str = "",
        category: str = "",
        intensity: str = "",
        occurred_at: Optional[datetime] = None
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='booking.attended',
            user_id=user_id,
            idempotency_key=f"booking:{booking_id}:check_in",
            occurred_at=occurred_at or datetime.now(timezone.utc),
            payload={
                'booking_id': str(booking_id),
                'session_id': str(session_id) if session_id else None,
                'class_name': class_name,
                'category': category,
                'intensity': intensity,
            }
        )

    @classmethod
    def create_workout_completed(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        workout_log_id: UUID,
        workout_name: str = "",
        duration_seconds: int = 0,
        occurred_at: Optional[datetime] = None
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='workout.completed',
            user_id=user_id,
            idempotency_key=f"workout_log:{workout_log_id}:completed",
            occurred_at=occurred_at or datetime.now(timezone.utc),
            payload={
                'workout_log_id': str(workout_log_id),
                'workout_name': workout_name,
                'duration_seconds': duration_seconds,
            }
        )

    @classmethod
    def create_payment_completed(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        payment_id: UUID,
        amount: float,
        payment_type: str = "package_purchase",
        occurred_at: Optional[datetime] = None
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='payment.completed',
            user_id=user_id,
            idempotency_key=f"payment:{payment_id}",
            occurred_at=occurred_at or datetime.now(timezone.utc),
            payload={
                'payment_id': str(payment_id),
                'amount': float(amount),
                'payment_type': payment_type,
            }
        )
