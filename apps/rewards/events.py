"""
Canonical Reward Domain Events

Typed, immutable data structures representing business actions emitted
from platform apps (scheduling, workout, payments, nutrition, users, social)
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

    # --------------------------------------------------------------------------
    # Scheduling & Booking Events
    # --------------------------------------------------------------------------
    @classmethod
    def create_booking_created(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        booking_id: UUID,
        class_name: str = "",
        category: str = ""
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='booking.created',
            user_id=user_id,
            idempotency_key=f"booking:{booking_id}:created",
            payload={'booking_id': str(booking_id), 'class_name': class_name, 'category': category}
        )

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
    def create_booking_cancelled(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        booking_id: UUID,
        is_late_cancel: bool = False
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='booking.cancelled',
            user_id=user_id,
            idempotency_key=f"booking:{booking_id}:cancelled",
            payload={'booking_id': str(booking_id), 'is_late_cancel': is_late_cancel}
        )

    # --------------------------------------------------------------------------
    # Facility Access & Check-In Events
    # --------------------------------------------------------------------------
    @classmethod
    def create_facility_access(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        location_id: Optional[UUID] = None,
        access_point: str = "main_turnstile"
    ) -> 'RewardEvent':
        import uuid
        event_id = uuid.uuid4()
        return cls(
            tenant_id=tenant_id,
            event_type='facility.access',
            user_id=user_id,
            idempotency_key=f"facility_access:{event_id}",
            payload={'location_id': str(location_id) if location_id else None, 'access_point': access_point}
        )

    # --------------------------------------------------------------------------
    # Workout Events
    # --------------------------------------------------------------------------
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
    def create_weight_logged(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        weight_entry_id: UUID,
        weight_kg: float
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='workout.weight_logged',
            user_id=user_id,
            idempotency_key=f"weight_entry:{weight_entry_id}",
            payload={'weight_entry_id': str(weight_entry_id), 'weight_kg': weight_kg}
        )

    # --------------------------------------------------------------------------
    # Nutrition & Meal Logging Events
    # --------------------------------------------------------------------------
    @classmethod
    def create_meal_logged(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        meal_id: UUID,
        meal_type: str = "lunch",
        calories: float = 0.0
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='nutrition.meal_logged',
            user_id=user_id,
            idempotency_key=f"meal:{meal_id}",
            payload={'meal_id': str(meal_id), 'meal_type': meal_type, 'calories': calories}
        )

    @classmethod
    def create_water_logged(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        log_id: UUID,
        amount_ml: int = 250
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='nutrition.water_logged',
            user_id=user_id,
            idempotency_key=f"water_log:{log_id}",
            payload={'amount_ml': amount_ml}
        )

    # --------------------------------------------------------------------------
    # Payment & Purchase Events
    # --------------------------------------------------------------------------
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

    # --------------------------------------------------------------------------
    # Referral & Social Events
    # --------------------------------------------------------------------------
    @classmethod
    def create_referral_completed(
        cls,
        tenant_id: UUID,
        referrer_id: UUID,
        referee_id: UUID
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='referral.completed',
            user_id=referrer_id,
            idempotency_key=f"referral:{referee_id}",
            payload={'referrer_id': str(referrer_id), 'referee_id': str(referee_id)}
        )

    @classmethod
    def create_social_post_created(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        post_id: UUID
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='social.post_created',
            user_id=user_id,
            idempotency_key=f"social_post:{post_id}",
            payload={'post_id': str(post_id)}
        )

    # --------------------------------------------------------------------------
    # Registration Events
    # --------------------------------------------------------------------------
    @classmethod
    def create_user_registered(
        cls,
        tenant_id: UUID,
        user_id: UUID,
        email: str = ""
    ) -> 'RewardEvent':
        return cls(
            tenant_id=tenant_id,
            event_type='user.registered',
            user_id=user_id,
            idempotency_key=f"user_register:{user_id}",
            payload={'user_id': str(user_id), 'email': email}
        )
