"""
Notification Domain Events

Lightweight dataclasses used as the interface between platform code
(scheduling, workout, payments) and the notification engine.

Platform code emits events. The notification engine consumes them.
This decouples business logic from notification delivery details.

Usage (from scheduling/views.py):
    from apps.notifications.events import BookingConfirmedEvent
    from apps.notifications.services import NotificationService

    NotificationService.handle_event(BookingConfirmedEvent(
        tenant_id=booking.tenant_id,
        recipient_id=booking.client_id,
        entity_id=booking.id,
        context_data={
            'client_name': booking.client.profile.first_name,
            'class_name': booking.session.template.name,
            'class_time': str(booking.session.start_at),
            'gym_name': booking.tenant.name,
        }
    ))

The booking view doesn't know about FCM, email, quiet hours, or delivery policies.
It only emits a typed event.
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from uuid import UUID


@dataclass
class NotificationEvent:
    """
    Base domain event for the notification engine.
    All platform events inherit from this.
    """
    tenant_id:    UUID
    recipient_id: UUID
    entity_id:    Optional[UUID] = None
    context_data: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def event_type(self) -> str:
        return getattr(self, '_event_type', 'unknown')


@dataclass
class BookingConfirmedEvent(NotificationEvent):
    """Emitted when a client books a class session."""
    _event_type = 'booking_confirmed'


@dataclass
class BookingCancelledEvent(NotificationEvent):
    """Emitted when a booking is cancelled."""
    _event_type = 'booking_cancelled'


@dataclass
class WaitlistOfferedEvent(NotificationEvent):
    """Emitted when a waitlist spot becomes available and is offered to a client."""
    _event_type = 'waitlist_offered'


@dataclass
class SessionCancelledEvent(NotificationEvent):
    """Emitted when a class session is cancelled by the gym."""
    _event_type = 'session_cancelled'


@dataclass
class ClassReminder24hEvent(NotificationEvent):
    """Emitted by the beat task for class reminders 24 hours ahead."""
    _event_type = 'class_reminder_24h'


@dataclass
class ClassReminder1hEvent(NotificationEvent):
    """Emitted by the beat task for class reminders 1 hour ahead."""
    _event_type = 'class_reminder_1h'


@dataclass
class AppointmentReminderEvent(NotificationEvent):
    """Emitted by the beat task for appointment reminders."""
    _event_type = 'appointment_reminder'


@dataclass
class WorkoutAssignedEvent(NotificationEvent):
    """Emitted when a trainer assigns a workout to a client."""
    _event_type = 'workout_assigned'


@dataclass
class PaymentSuccessEvent(NotificationEvent):
    """Emitted on successful payment."""
    _event_type = 'payment_success'


@dataclass
class PaymentFailedEvent(NotificationEvent):
    """
    Emitted on payment failure.
    Note: PaymentFailed uses PUSH_AND_EMAIL (template.is_critical=True).
    """
    _event_type = 'payment_failed'


@dataclass
class MembershipExpiringEvent(NotificationEvent):
    """Emitted by the beat task for membership expiry warnings (7d, 3d, 1d)."""
    _event_type = 'membership_expiring'
