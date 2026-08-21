from django.shortcuts import get_object_or_404
from django.db import transaction, models
from django.db.models import Q, Count, F
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend
import logging
from datetime import datetime, timedelta, time, timezone as datetime_timezone

from .models import (
    Location, Room, StaffLocation, StaffAvailability, ClassTemplate,
    RecurrenceRule, ClassSession, Booking, Appointment, Waitlist,
    SubstituteRequest, PackageType, Package, Payment, CancellationPolicy,
    StaffClientAssignment
)
from .serializers import (
    LocationSerializer, RoomSerializer, StaffLocationSerializer,
    StaffAvailabilitySerializer, ClassTemplateSerializer, RecurrenceRuleSerializer,
    ClassSessionSerializer, BookingCreateSerializer, BookingReadSerializer,
    BookingEditSerializer, AppointmentSerializer, WaitlistSerializer,
    SubstituteRequestSerializer, PackageTypeSerializer, PackageSerializer,
    PaymentSerializer, CancellationPolicySerializer,
    StaffAssignClientSerializer
)
from .permissions import (
    IsAuthenticated, IsOwnerOrManager, IsGymStaffOrOwner, IsFrontDeskOrAdmin,
    IsInstructor, IsClient, IsAssignedClient
)
from apps.users.models import User, UserRole

logger = logging.getLogger(__name__)

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class LocationViewSet(viewsets.ModelViewSet):
    queryset = Location.all_objects.all()
    serializer_class = LocationSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsOwnerOrManager()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = Location.objects.all()
        user = self.request.user
        if getattr(user, 'role', None) in [UserRole.GYM_MANAGER, UserRole.TRAINER, UserRole.FRONT_DESK]:
            qs = qs.filter(location_staff__staff=user).distinct()
        return qs


class RoomViewSet(viewsets.ModelViewSet):
    queryset = Room.all_objects.all()
    serializer_class = RoomSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsOwnerOrManager()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = Room.objects.all()
        user = self.request.user
        
        # Staff location segregation
        if getattr(user, 'role', None) in [UserRole.GYM_MANAGER, UserRole.TRAINER, UserRole.FRONT_DESK]:
            qs = qs.filter(location__location_staff__staff=user).distinct()
            
        location_id = self.request.query_params.get('location')
        if location_id:
            qs = qs.filter(location_id=location_id)
        return qs


class StaffLocationViewSet(viewsets.ModelViewSet):
    queryset = StaffLocation.all_objects.all()
    serializer_class = StaffLocationSerializer
    permission_classes = [IsOwnerOrManager]

    def get_queryset(self):
        return StaffLocation.objects.all()


class StaffAvailabilityViewSet(viewsets.ModelViewSet):
    queryset = StaffAvailability.all_objects.all()
    serializer_class = StaffAvailabilitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = StaffAvailability.objects.all()
        staff_id = self.request.query_params.get('staff')
        if staff_id:
            qs = qs.filter(staff_id=staff_id)
        return qs


class ClassTemplateViewSet(viewsets.ModelViewSet):
    queryset = ClassTemplate.all_objects.all()
    serializer_class = ClassTemplateSerializer
    permission_classes = [IsOwnerOrManager]

    def get_queryset(self):
        return ClassTemplate.objects.all()


class RecurrenceRuleViewSet(viewsets.ModelViewSet):
    queryset = RecurrenceRule.all_objects.all()
    serializer_class = RecurrenceRuleSerializer
    permission_classes = [IsOwnerOrManager]

    def get_queryset(self):
        return RecurrenceRule.objects.all()

    @transaction.atomic
    def perform_create(self, serializer):
        rule = serializer.save()
        
        # Expand RecurrenceRule into ClassSession rows
        start_date = rule.start_date
        end_date = rule.end_date
        days_of_week = [d.lower() for d in rule.days_of_week]
        
        current_date = start_date
        sessions_to_create = []

        while current_date <= end_date:
            weekday_name = current_date.strftime('%A').lower()
            if weekday_name in days_of_week:
                # Build start_at and end_at in UTC
                naive_start = datetime.combine(current_date, rule.start_time)
                # Assume UTC timezone for storage
                start_at = timezone.make_aware(naive_start, datetime_timezone.utc)
                end_at = start_at + timedelta(minutes=rule.template.duration_min)

                # Check conflict
                # Room Conflict check
                if rule.room:
                    room_conflict = ClassSession.objects.filter(
                        room=rule.room,
                        start_at__lt=end_at,
                        end_at__gt=start_at,
                        status='scheduled'
                    ).exists()
                    if room_conflict:
                        raise ValidationError(f"Room conflict detected for {rule.room} on {current_date} at {rule.start_time}")

                # Staff Conflict check
                if rule.staff:
                    staff_conflict = ClassSession.objects.filter(
                        staff=rule.staff,
                        start_at__lt=end_at,
                        end_at__gt=start_at,
                        status='scheduled'
                    ).exists()
                    if staff_conflict:
                        raise ValidationError(f"Staff conflict detected for {rule.staff.email} on {current_date} at {rule.start_time}")

                sessions_to_create.append(
                    ClassSession(
                        tenant=rule.tenant,
                        template=rule.template,
                        recurrence_rule=rule,
                        room=rule.room,
                        staff=rule.staff,
                        start_at=start_at,
                        end_at=end_at,
                        capacity=rule.template.default_capacity
                    )
                )

            current_date += timedelta(days=1)

        ClassSession.objects.bulk_create(sessions_to_create)


class ClassSessionViewSet(viewsets.ModelViewSet):
    queryset = ClassSession.all_objects.all()
    serializer_class = ClassSessionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsOwnerOrManager()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = ClassSession.objects.select_related('template', 'room', 'staff', 'staff__profile')
        user = self.request.user
        
        # Staff location segregation
        if getattr(user, 'role', None) in [UserRole.GYM_MANAGER, UserRole.TRAINER, UserRole.FRONT_DESK]:
            qs = qs.filter(template__location__location_staff__staff=user).distinct()
            
        params = self.request.query_params
        
        # 1. Location filter
        location = params.get('location') or params.get('locationId') or params.get('location_id')
        if location:
            qs = qs.filter(template__location_id=location)
            
        # 2. Date filters (handling single date, date range, or timestamps without midnight cutoff)
        single_date = params.get('date')
        if single_date:
            d = parse_date(single_date) or single_date[:10]
            qs = qs.filter(start_at__date=d)
        else:
            date_from = params.get('date_from') or params.get('start_date')
            date_to = params.get('date_to') or params.get('end_date')
            
            if date_from:
                # If length is 10 (e.g. YYYY-MM-DD), use date__gte to avoid time format mismatch
                if len(str(date_from)) == 10 and parse_date(str(date_from)):
                    qs = qs.filter(start_at__date__gte=parse_date(str(date_from)))
                else:
                    qs = qs.filter(start_at__gte=parse_datetime(str(date_from)) or date_from)
                    
            if date_to:
                # If length is 10 (e.g. YYYY-MM-DD), use date__lte to include the entire end day up to 23:59:59
                if len(str(date_to)) == 10 and parse_date(str(date_to)):
                    qs = qs.filter(start_at__date__lte=parse_date(str(date_to)))
                else:
                    qs = qs.filter(start_at__lte=parse_datetime(str(date_to)) or date_to)
                    
        # 3. Status filter
        status_param = params.get('status')
        if status_param and status_param.lower() != 'all':
            if status_param.lower() == 'active':
                qs = qs.filter(status='scheduled')
            else:
                qs = qs.filter(status__iexact=status_param)
                
        # 4. Staff / Trainer filter
        staff_id = params.get('staff') or params.get('staff_id') or params.get('trainer') or params.get('trainer_id')
        if staff_id:
            qs = qs.filter(staff_id=staff_id)
            
        # 5. Template / Class filter
        template_id = params.get('template') or params.get('template_id') or params.get('class_id') or params.get('class_template')
        if template_id:
            qs = qs.filter(template_id=template_id)
            
        # 6. Room filter
        room_id = params.get('room') or params.get('room_id')
        if room_id:
            qs = qs.filter(room_id=room_id)

        return qs

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        """
        Cancel a session. Triggers credit-refund and notifications.
        """
        session = self.get_object()
        session.status = 'cancelled'
        session.save()

        # Import celery tasks inline to prevent circular dependencies
        from .tasks import process_credit_refund_job
        process_credit_refund_job.delay(str(session.id))

        return Response({"detail": "Session cancelled successfully and refund job triggered."}, status=status.HTTP_200_OK)


class BookingViewSet(viewsets.ModelViewSet):
    queryset = Booking.all_objects.all()
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_serializer_class(self):
        if self.action == 'create':
            return BookingCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return BookingEditSerializer
        return BookingReadSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Booking.objects.select_related('session', 'session__template', 'client')
        if user.role == UserRole.CLIENT:
            qs = qs.filter(client=user)
        elif user.role in [UserRole.GYM_MANAGER, UserRole.FRONT_DESK]:
            qs = qs.filter(session__template__location__location_staff__staff=user).distinct()
        elif user.role == UserRole.TRAINER:
            qs = qs.filter(Q(session__staff=user) | Q(session__template__location__location_staff__staff=user)).distinct()
        
        client_id = self.request.query_params.get('client')
        if client_id:
            qs = qs.filter(client_id=client_id)
        return qs

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        Concurrently secure a booking.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session_id = serializer.validated_data['session'].id

        # Determine target booking client
        from apps.users.models import UserRole
        is_staff = request.user.is_staff or request.user.role != UserRole.CLIENT
        
        target_client = request.user
        if is_staff:
            if serializer.validated_data.get('client'):
                target_client = serializer.validated_data['client']
            else:
                return Response({"detail": "client field is required when booking as staff."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            # Client cannot book for someone else
            if serializer.validated_data.get('client') and serializer.validated_data['client'] != request.user:
                return Response({"detail": "You cannot make bookings on behalf of other clients."}, status=status.HTTP_403_FORBIDDEN)

        # Row lock session
        session = ClassSession.objects.select_for_update().get(id=session_id)

        if session.status != 'scheduled':
            return Response({"detail": "Session is not scheduled."}, status=status.HTTP_400_BAD_REQUEST)

        # Check existing booking
        existing_booking = Booking.objects.filter(client=target_client, session=session).first()
        if existing_booking and existing_booking.status == 'booked':
            return Response({"detail": "Already booked this session."}, status=status.HTTP_400_BAD_REQUEST)

        # Capacity Check
        current_bookings = session.bookings.filter(status='booked').count()
        if current_bookings >= session.capacity:
            return Response({"detail": "Session is full. Join waitlist instead."}, status=status.HTTP_400_BAD_REQUEST)

        # Resolve Payment: find active Package or raise error
        package = Package.objects.select_for_update().filter(
            client=target_client,
            credits_remaining__gt=0,
            expires_at__gt=timezone.now()
        ).first()

        if not package:
            return Response({"detail": "No active credits or packages found for booking."}, status=status.HTTP_402_PAYMENT_REQUIRED)

        # Deduct Credit
        package.credits_remaining -= 1
        package.save()

        if existing_booking:
            # Reactivate existing booking row to satisfy unique_together constraint
            existing_booking.status = 'booked'
            existing_booking.credit_source = package
            existing_booking.join_mode = serializer.validated_data.get('join_mode', 'physical')
            existing_booking.music_preference = serializer.validated_data.get('music_preference', '')
            existing_booking.save()
            booking = existing_booking
        else:
            # Create Booking
            booking = Booking.objects.create(
                tenant=request.tenant,
                client=target_client,
                session=session,
                credit_source=package,
                status='booked',
                join_mode=serializer.validated_data.get('join_mode', 'physical'),
                music_preference=serializer.validated_data.get('music_preference', '')
            )

        # Create confirmation notification
        from apps.notifications.services import NotificationService
        from apps.notifications.events import BookingConfirmedEvent
        NotificationService.handle_event(BookingConfirmedEvent(
            tenant_id=request.tenant.id,
            recipient_id=target_client.id,
            entity_id=booking.id,
            context_data={
                'client_name': target_client.profile.first_name if hasattr(target_client, 'profile') else target_client.email,
                'class_name': session.template.name,
                'class_time': str(session.start_at),
                'gym_name': request.tenant.name,
            }
        ))

        return Response(BookingReadSerializer(booking).data, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        """
        Clients calling cancellation.
        """
        booking = self.get_object()
        if booking.status == 'cancelled':
            return Response({"detail": "Booking is already cancelled."}, status=status.HTTP_400_BAD_REQUEST)

        session = booking.session
        now = timezone.now()

        # Resolve cancellation policy (most specific first: template > global)
        policy = CancellationPolicy.objects.filter(template=session.template).first()
        if not policy:
            policy = CancellationPolicy.objects.filter(scope_type='global').first()

        cutoff_hours = policy.cutoff_hours if policy else 24
        cutoff_time = session.start_at - timedelta(hours=cutoff_hours)

        is_early_cancel = now <= cutoff_time

        booking.status = 'cancelled'
        booking.save()

        if is_early_cancel:
            # Refund Credit
            if booking.credit_source:
                pkg = Package.objects.select_for_update().get(id=booking.credit_source.id)
                pkg.credits_remaining += 1
                pkg.save()
        else:
            # Late cancellation: Forfeit credit (we keep the booking status as 'cancelled' but do not refund package credit)
            pass

        # Trigger WaitlistPromotionJob
        from .tasks import process_waitlist_promotion_job
        process_waitlist_promotion_job.delay(str(session.id))

        return Response({
            "status": "cancelled",
            "refunded": is_early_cancel,
            "detail": "Booking cancelled."
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def check_in(self, request, pk=None):
        booking = self.get_object()
        user = request.user
        
        # Verify if user is staff OR the owner of the booking
        is_staff = user.role in [UserRole.PLATFORM_ADMIN, UserRole.GYM_OWNER, UserRole.GYM_MANAGER, UserRole.FRONT_DESK, UserRole.TRAINER]
        if not is_staff and booking.client != user:
            return Response({"detail": "You do not have permission to check in for this booking."}, status=status.HTTP_403_FORBIDDEN)

        booking.checked_in_at = timezone.now()
        booking.status = 'attended'
        booking.save()
        return Response({"detail": "Checked in successfully."})

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    def check_out(self, request, pk=None):
        booking = self.get_object()
        user = request.user
        
        # Verify if user is staff OR the owner of the booking
        is_staff = user.role in [UserRole.PLATFORM_ADMIN, UserRole.GYM_OWNER, UserRole.GYM_MANAGER, UserRole.FRONT_DESK, UserRole.TRAINER]
        if not is_staff and booking.client != user:
            return Response({"detail": "You do not have permission to check out for this booking."}, status=status.HTTP_403_FORBIDDEN)

        booking.checked_out_at = timezone.now()
        booking.save()
        return Response({"detail": "Checked out successfully."})


class WaitlistViewSet(viewsets.ModelViewSet):
    queryset = Waitlist.all_objects.all()
    serializer_class = WaitlistSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Waitlist.objects.all().select_related('client', 'session', 'session__template')
        if user.role == UserRole.CLIENT:
            qs = qs.filter(client=user)
        
        client_id = self.request.query_params.get('client')
        if client_id:
            qs = qs.filter(client_id=client_id)
        return qs

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        session_id = request.data.get('session')
        session = get_object_or_404(ClassSession, id=session_id)

        # Determine target waitlist client
        from apps.users.models import UserRole
        is_staff = request.user.is_staff or request.user.role != UserRole.CLIENT

        target_client = request.user
        if is_staff:
            client_id = request.data.get('client')
            if client_id:
                target_client = get_object_or_404(User, id=client_id)
            else:
                return Response({"detail": "client field is required when adding to waitlist as staff."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            if request.data.get('client') and str(request.data.get('client')) != str(request.user.id):
                return Response({"detail": "You cannot add other clients to the waitlist."}, status=status.HTTP_403_FORBIDDEN)

        # Check existing waitlist or booking
        if Waitlist.objects.filter(client=target_client, session=session, status='waiting').exists():
            return Response({"detail": "Already on waitlist."}, status=status.HTTP_400_BAD_REQUEST)

        # Calculate position
        max_pos = Waitlist.objects.filter(session=session, status='waiting').aggregate(models.Max('position'))['position__max']
        next_pos = (max_pos or 0) + 1

        waitlist = Waitlist.objects.create(
            tenant=request.tenant,
            client=target_client,
            session=session,
            position=next_pos,
            status='waiting'
        )

        return Response(WaitlistSerializer(waitlist).data, status=status.HTTP_201_CREATED)


class AppointmentViewSet(viewsets.ModelViewSet):
    queryset = Appointment.all_objects.all()
    serializer_class = AppointmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Appointment.objects.select_related('provider', 'client')
        if user.role == UserRole.CLIENT:
            qs = qs.filter(client=user)
        elif user.role in [UserRole.GYM_MANAGER, UserRole.FRONT_DESK]:
            qs = qs.filter(location__location_staff__staff=user).distinct()
        elif user.role == UserRole.TRAINER:
            qs = qs.filter(Q(provider=user) | Q(location__location_staff__staff=user)).distinct()
        
        client_id = self.request.query_params.get('client')
        if client_id:
            qs = qs.filter(client_id=client_id)
        return qs

    @action(detail=False, methods=['get'], url_path='availability')
    def provider_availability(self, request):
        provider_id = request.query_params.get('provider')
        if not provider_id:
            return Response({"detail": "provider query param required."}, status=400)
        
        provider = get_object_or_404(User, id=provider_id, role__in=['trainer', 'gym_owner', 'gym_manager'])
        
        # Calculate availability for the next 7 days
        today = timezone.now().date()
        slots = []

        availabilities = StaffAvailability.objects.filter(staff=provider, is_blackout=False)
        blackouts = StaffAvailability.objects.filter(staff=provider, is_blackout=True)

        for i in range(7):
            current_date = today + timedelta(days=i)
            weekday_name = current_date.strftime('%A').lower()

            # Find matching availabilities
            day_avails = availabilities.filter(weekday_or_date__in=[weekday_name, str(current_date)])
            day_blackouts = blackouts.filter(weekday_or_date__in=[weekday_name, str(current_date)])

            if not day_avails.exists() or day_blackouts.exists():
                continue

            for avail in day_avails:
                # Divide day into 1-hour slots
                start_time = avail.start_time
                end_time = avail.end_time

                current_time = datetime.combine(current_date, start_time)
                limit_time = datetime.combine(current_date, end_time)

                while current_time + timedelta(hours=1) <= limit_time:
                    slot_start = timezone.make_aware(current_time, datetime_timezone.utc)
                    slot_end = timezone.make_aware(current_time + timedelta(hours=1), datetime_timezone.utc)

                    # Check conflict with existing appointments or class sessions
                    overlap_appt = Appointment.objects.filter(
                        provider=provider,
                        start_at__lt=slot_end,
                        end_at__gt=slot_start,
                        status='scheduled'
                    ).exists()

                    overlap_session = ClassSession.objects.filter(
                        staff=provider,
                        start_at__lt=slot_end,
                        end_at__gt=slot_start,
                        status='scheduled'
                    ).exists()

                    if not overlap_appt and not overlap_session:
                        slots.append({
                            "start_at": slot_start,
                            "end_at": slot_end
                        })

                    current_time += timedelta(hours=1)

        return Response({"availability": slots})

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """
        Book a 1-on-1 Appointment.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        provider = data['provider']
        start_at = data['start_at']
        end_at = data['end_at']

        # Determine target appointment client
        from apps.users.models import UserRole
        is_staff = request.user.is_staff or request.user.role != UserRole.CLIENT

        target_client = request.user
        if is_staff:
            if data.get('client'):
                target_client = data['client']
            else:
                return Response({"detail": "client field is required when booking appointments as staff."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            if data.get('client') and data['client'] != request.user:
                return Response({"detail": "You cannot book appointments on behalf of other clients."}, status=status.HTTP_403_FORBIDDEN)

        # Conflict check
        overlap_appt = Appointment.objects.filter(
            provider=provider,
            start_at__lt=end_at,
            end_at__gt=start_at,
            status='scheduled'
        ).exists()

        overlap_session = ClassSession.objects.filter(
            staff=provider,
            start_at__lt=end_at,
            end_at__gt=start_at,
            status='scheduled'
        ).exists()

        if overlap_appt or overlap_session:
            return Response({"detail": "Provider is not available during this time slot."}, status=status.HTTP_400_BAD_REQUEST)

        # Check credits
        package = Package.objects.select_for_update().filter(
            client=target_client,
            credits_remaining__gt=0,
            expires_at__gt=timezone.now()
        ).first()

        if not package:
            return Response({"detail": "No active credits/packages found to book appointment."}, status=status.HTTP_402_PAYMENT_REQUIRED)

        package.credits_remaining -= 1
        package.save()

        appointment = Appointment.objects.create(
            tenant=request.tenant,
            client=target_client,
            provider=provider,
            location=data['location'],
            room=data.get('room'),
            start_at=start_at,
            end_at=end_at,
            credit_source=package,
            status='scheduled'
        )

        return Response(AppointmentSerializer(appointment).data, status=status.HTTP_201_CREATED)


class SubstituteRequestViewSet(viewsets.ModelViewSet):
    queryset = SubstituteRequest.all_objects.all()
    serializer_class = SubstituteRequestSerializer
    permission_classes = [IsGymStaffOrOwner]

    def get_queryset(self):
        return SubstituteRequest.objects.all()

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        session_id = request.data.get('session')
        session = get_object_or_404(ClassSession, id=session_id)

        # Determine if user is owner/manager
        from apps.users.models import UserRole
        is_admin = request.user.role in [UserRole.GYM_OWNER, UserRole.GYM_MANAGER] or request.user.is_staff

        # Only session leader can open substitution request (unless they are admin)
        if not is_admin and session.staff != request.user:
            return Response({"detail": "You can only request substitution for classes you lead."}, status=status.HTTP_403_FORBIDDEN)

        sub_req = SubstituteRequest.objects.create(
            tenant=request.tenant,
            session=session,
            requested_by_staff=session.staff,
            status='open'
        )

        # Trigger SubstituteBroadcastJob
        from .tasks import process_substitute_broadcast_job
        process_substitute_broadcast_job.delay(str(sub_req.id))

        return Response(SubstituteRequestSerializer(sub_req).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], permission_classes=[IsGymStaffOrOwner])
    @transaction.atomic
    def accept(self, request, pk=None):
        sub_req = SubstituteRequest.objects.select_for_update().get(id=pk)

        if sub_req.status != 'open':
            return Response({"detail": "Request has already been filled or expired."}, status=status.HTTP_400_BAD_REQUEST)

        # Determine target trainer who covers
        from apps.users.models import UserRole
        is_admin = request.user.role in [UserRole.GYM_OWNER, UserRole.GYM_MANAGER] or request.user.is_staff

        target_trainer = request.user
        if is_admin:
            if request.data.get('trainer'):
                trainer_id = request.data.get('trainer')
                target_trainer = get_object_or_404(User, id=trainer_id)
            else:
                return Response({"detail": "trainer field is required when filling substitution as admin."}, status=status.HTTP_400_BAD_REQUEST)
        else:
            # Normal trainer can only accept for themselves
            if request.data.get('trainer') and str(request.data.get('trainer')) != str(request.user.id):
                return Response({"detail": "You cannot accept substitution requests on behalf of other trainers."}, status=status.HTTP_403_FORBIDDEN)

        # Update substitute request
        sub_req.status = 'filled'
        sub_req.accepted_by_staff = target_trainer
        sub_req.save()

        # Update session
        session = sub_req.session
        session.staff = target_trainer
        session.save()

        return Response({"status": "filled", "accepted_by": target_trainer.email})


class PackageTypeViewSet(viewsets.ModelViewSet):
    queryset = PackageType.all_objects.all()
    serializer_class = PackageTypeSerializer
    permission_classes = [IsOwnerOrManager]

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsAuthenticated()]
        return [IsOwnerOrManager()]

    def get_queryset(self):
        qs = PackageType.objects.all()
        location_id = self.request.query_params.get('location')
        if location_id:
            qs = qs.filter(location_id=location_id)
        return qs

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        from apps.payments.stripe_package_service import StripePackageService
         
        StripePackageService.archive_package_on_stripe(instance)

        if instance.purchased_packages.exists():
            instance.is_active = False
            instance.save(update_fields=['is_active'])
            return Response(status=status.HTTP_204_NO_CONTENT)
        else:
            return super().destroy(request, *args, **kwargs)


class PackageViewSet(viewsets.ModelViewSet):
    queryset = Package.all_objects.all()
    serializer_class = PackageSerializer
    permission_classes = [IsOwnerOrManager]

    def get_permissions(self):
        if self.action == 'my_active_packages':
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = Package.objects.all().select_related('package_type', 'client')
        client_id = self.request.query_params.get('client')
        if client_id:
            qs = qs.filter(client_id=client_id)
        return qs

    @action(detail=False, methods=['get'], url_path='my-active-packages')
    def my_active_packages(self, request):
        packages = Package.objects.filter(
            client=request.user,
            credits_remaining__gt=0,
            expires_at__gt=timezone.now()
        ).select_related('package_type')
        return Response(PackageSerializer(packages, many=True).data)


class ReportsView(APIView):
    permission_classes = [IsFrontDeskOrAdmin]

    def get(self, request):
        report_type = request.query_params.get('type')
        location_id = request.query_params.get('location')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        if not report_type:
            return Response({"detail": "type query param is required (fill-rate, no-show, staff-utilization)."}, status=400)

        # Default dates
        if not start_date:
            start_date = timezone.now().date() - timedelta(days=30)
        else:
            start_date = parse_date(start_date)

        if not end_date:
            end_date = timezone.now().date()
        else:
            end_date = parse_date(end_date)

        if report_type == 'fill-rate':
            # Count bookings on completed/scheduled sessions
            sessions = ClassSession.objects.filter(
                start_at__date__gte=start_date,
                start_at__date__lte=end_date
            )
            if location_id:
                sessions = sessions.filter(template__location_id=location_id)

            data = []
            for s in sessions:
                booked_count = s.bookings.filter(status='booked').count()
                data.append({
                    "session_id": s.id,
                    "title": s.template.name,
                    "date": s.start_at.date(),
                    "capacity": s.capacity,
                    "booked": booked_count,
                    "fill_rate": round((booked_count / s.capacity) * 100, 2) if s.capacity > 0 else 0.0
                })
            return Response(data)

        elif report_type == 'no-show':
            bookings = Booking.objects.filter(
                session__start_at__date__gte=start_date,
                session__start_at__date__lte=end_date
            )
            if location_id:
                bookings = bookings.filter(session__template__location_id=location_id)

            total = bookings.count()
            no_shows = bookings.filter(status='no_show').count()
            no_show_rate = round((no_shows / total) * 100, 2) if total > 0 else 0.0

            return Response({
                "total_bookings": total,
                "total_no_shows": no_shows,
                "no_show_rate_percent": no_show_rate
            })

        elif report_type == 'staff-utilization':
            # Available hours vs Booked hours
            instructors = User.objects.filter(role__in=['trainer', 'gym_owner', 'gym_manager'])
            if location_id:
                instructors = instructors.filter(staff_locations__location_id=location_id)

            data = []
            for inst in instructors:
                # Find available hours from StaffAvailability
                avails = StaffAvailability.objects.filter(staff=inst, is_blackout=False)
                # For MVP: simple calculation of avail hours (summing up avail windows)
                total_avail_hours = 0
                for a in avails:
                    dummy_start = datetime.combine(timezone.now().date(), a.start_time)
                    dummy_end = datetime.combine(timezone.now().date(), a.end_time)
                    total_avail_hours += (dummy_end - dummy_start).total_seconds() / 3600.0

                # Booked hours from class sessions
                sessions = ClassSession.objects.filter(
                    staff=inst,
                    start_at__date__gte=start_date,
                    start_at__date__lte=end_date,
                    status='scheduled'
                )
                booked_hours = sum([s.template.duration_min for s in sessions]) / 60.0

                data.append({
                    "instructor_id": inst.id,
                    "email": inst.email,
                    "weekly_available_hours": total_avail_hours,
                    "booked_session_hours": booked_hours,
                    "utilization_rate_percent": round((booked_hours / (total_avail_hours * 4)) * 100, 2) if total_avail_hours > 0 else 0.0
                })
            return Response(data)

        return Response({"detail": "Invalid report type."}, status=400)


class StaffAssignmentViewSet(viewsets.ModelViewSet):
    queryset = StaffClientAssignment.objects.all()
    serializer_class = StaffAssignClientSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    
    def get_queryset(self):
        user = self.request.user
        queryset = StaffClientAssignment.objects.all().select_related('staff__profile', 'client__profile')
        if user.role == UserRole.TRAINER:
            return queryset.filter(staff=user)
        elif user.role in [UserRole.GYM_OWNER, UserRole.GYM_MANAGER]:
            return queryset
        return queryset.none()
    
    @action(detail=False, methods=['post'], url_path='bulk-assign')
    def bulk_assign(self, request):
        staff_id = request.data.get('staff')
        client_ids = request.data.get('clients', [])

        staff = get_object_or_404(User, id=staff_id, role='trainer', tenant=request.tenant)
        valid_clients = User.objects.filter(
            id__in=client_ids, 
            role='client', 
            tenant=request.tenant
        ).values_list('id', flat=True)

        invalid_ids = set(client_ids) - set([str(cid) for cid in valid_clients])
        if invalid_ids:
            return Response(
                {"detail": f"Invalid client IDs: {list(invalid_ids)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        assignments = [
            StaffClientAssignment(
                staff=staff, 
                client_id=c_id, 
                tenant=request.tenant
            ) for c_id in valid_clients
        ]
        
        StaffClientAssignment.objects.bulk_create(assignments, ignore_conflicts=True)
        
        return Response({
            "detail": f"Successfully processed {len(valid_clients)} assignments."
        }, status=status.HTTP_200_OK)


class ViewAllClientsAPIView(APIView):
    """
    Legacy endpoint support for frontend fetching all active clients.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        status_filter = request.query_params.get('status', 'Active')
        
        qs = User.objects.filter(role='client')
        if request.user.tenant:
            qs = qs.filter(tenant=request.user.tenant)

        if status_filter.lower() == 'active':
            qs = qs.filter(is_active=True)
            
        # Basic serialization to match typical legacy client list shape
        data = []
        for user in qs:
            data.append({
                "id": user.id,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "status": "Active" if user.is_active else "Inactive",
                "phone_number": getattr(user.profile, "phone_number", "") if hasattr(user, "profile") else ""
            })
            
        return Response(data, status=status.HTTP_200_OK)

class FacilityAccessViewSet(viewsets.ModelViewSet):
    from .models import FacilityAccessLog
    from .serializers import FacilityAccessLogSerializer
    queryset = FacilityAccessLog.all_objects.all()
    serializer_class = FacilityAccessLogSerializer
    permission_classes = [IsFrontDeskOrAdmin]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = self.queryset.select_related('client', 'location')
        user = self.request.user
        if getattr(user, 'role', None) in [UserRole.GYM_MANAGER, UserRole.FRONT_DESK]:
            qs = qs.filter(location__location_staff__staff=user).distinct()
        return qs

    @action(detail=True, methods=['post'])
    def check_out(self, request, pk=None):
        access_log = self.get_object()
        if access_log.checked_out_at:
            return Response({"detail": "Already checked out."}, status=status.HTTP_400_BAD_REQUEST)
        access_log.checked_out_at = timezone.now()
        access_log.save()
        return Response({"detail": "Checked out successfully."})


class UpdateBookingAttributesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        booking_id = request.data.get('booking_id') or request.data.get('booking')
        if not booking_id:
            return Response({"error": "booking_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            booking = Booking.objects.get(id=booking_id)
        except (Booking.DoesNotExist, ValueError):
            return Response({"error": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        from apps.users.models import UserRole
        if request.user.role == UserRole.CLIENT and booking.client != request.user:
            return Response({"error": "You do not have permission to modify this booking."}, status=status.HTTP_403_FORBIDDEN)

        # Edit join_mode / attendance type
        if 'join_mode' in request.data:
            booking.join_mode = request.data['join_mode']
        elif 'attendance_type' in request.data:
            booking.join_mode = request.data['attendance_type']

        # Edit music preference
        if 'music_preference' in request.data:
            booking.music_preference = request.data['music_preference']

        booking.save()

        from .serializers import BookingReadSerializer
        return Response(BookingReadSerializer(booking, context={'request': request}).data, status=status.HTTP_200_OK)
