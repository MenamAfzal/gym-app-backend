from django.shortcuts import get_object_or_404
from django.db import transaction, models
from django.db.models import Q, Count, F
from django.utils import timezone
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import viewsets, status, permissions, serializers, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend
import logging
from datetime import datetime, timedelta, time, timezone as datetime_timezone

from .models import (
    Location, Room, SpotType, RoomLayout, Spot, StaffLocation, StaffAvailability, ClassTemplate,
    RecurrenceRule, ClassSession, Booking, Appointment, Waitlist,
    SubstituteRequest, PackageType, Package, Payment, CancellationPolicy,
    StaffClientAssignment
)
from .serializers import (
    LocationSerializer, RoomSerializer, SpotTypeSerializer, RoomLayoutSerializer,
    RoomLayoutCreateSerializer, BookingChangeSpotSerializer, StaffLocationSerializer,
    StaffAvailabilitySerializer, ClassTemplateSerializer, RecurrenceRuleSerializer,
    ClassSessionSerializer, BookingCreateSerializer, BookingReadSerializer,
    BookingEditSerializer, AppointmentSerializer, WaitlistSerializer,
    SubstituteRequestSerializer, PackageTypeSerializer, PackageSerializer,
    PaymentSerializer, CancellationPolicySerializer,
    StaffAssignClientSerializer
)
from .services import create_layout, update_layout, change_booking_spot, SpotUnavailableError
from .permissions import (
    IsAuthenticated, IsOwnerOrManager, IsGymStaffOrOwner, IsFrontDeskOrAdmin,
    IsInstructor, IsClient, IsAssignedClient
)
from apps.users.models import User, UserRole

logger = logging.getLogger(__name__)

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 1000

    def paginate_queryset(self, queryset, request, view=None):
        params = getattr(request, 'query_params', getattr(request, 'GET', {}))
        pagination_param = params.get('pagination', '').lower()
        if pagination_param in ['false', '0', 'no']:
            return None
        return super().paginate_queryset(queryset, request, view)

    def get_page_size(self, request):
        if self.page_size_query_param:
            params = getattr(request, 'query_params', getattr(request, 'GET', {}))
            val = params.get(self.page_size_query_param)
            if val and str(val).lower() == 'all':
                return self.max_page_size
        return super().get_page_size(request)


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
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'restore']:
            return [IsOwnerOrManager()]
        return [IsAuthenticated()]

    def get_queryset(self):
        include_deleted = self.request.query_params.get('include_deleted') == '1'
        if include_deleted:
            qs = Room.objects.select_related('location').all()
        else:
            qs = Room.objects.alive().select_related('location')

        user = self.request.user
        if getattr(user, 'role', None) in [UserRole.GYM_MANAGER, UserRole.TRAINER, UserRole.FRONT_DESK]:
            qs = qs.filter(location__location_staff__staff=user).distinct()
            
        location_id = self.kwargs.get('location_pk') or self.request.query_params.get('location')
        if location_id:
            qs = qs.filter(location_id=location_id)
        return qs

    def perform_create(self, serializer):
        tenant = getattr(self.request, 'tenant', None) or self.request.user.tenant
        location_pk = self.kwargs.get('location_pk')
        if location_pk and not serializer.validated_data.get('location'):
            serializer.save(tenant=tenant, location_id=location_pk)
        elif serializer.validated_data.get('location'):
            serializer.save(tenant=tenant)
        else:
            raise serializers.ValidationError({"location": "Location is required."})

    @action(detail=True, methods=['post'], permission_classes=[IsOwnerOrManager])
    def restore(self, request, pk=None):
        room = Room.all_objects.get(id=pk)
        room.restore()
        return Response(RoomSerializer(room).data)


class SpotTypeViewSet(viewsets.ModelViewSet):
    serializer_class = SpotTypeSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsOwnerOrManager()]
        return [IsAuthenticated()]

    def get_queryset(self):
        qs = SpotType.objects.select_related('location').all()
        location_id = self.request.query_params.get('location')
        if location_id:
            qs = qs.filter(location_id=location_id)
        return qs

    def perform_create(self, serializer):
        tenant = getattr(self.request, 'tenant', None) or self.request.user.tenant
        serializer.save(tenant=tenant)


class RoomLayoutViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy', 'restore']:
            return [IsOwnerOrManager()]
        return [IsAuthenticated()]

    def get_serializer_class(self):
        if self.action == 'create':
            return RoomLayoutCreateSerializer
        return RoomLayoutSerializer

    def get_queryset(self):
        include_deleted = self.request.query_params.get('include_deleted') == '1'
        if include_deleted:
            qs = RoomLayout.objects.select_related('room', 'room__location')
        else:
            qs = RoomLayout.objects.alive().select_related('room', 'room__location')

        qs = qs.prefetch_related('spots__spot_type')
        room_id = self.kwargs.get('room_pk') or self.request.query_params.get('room')
        if room_id:
            qs = qs.filter(room_id=room_id)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = getattr(request, 'tenant', None) or request.user.tenant
        data = serializer.validated_data.copy()

        if 'room_pk' in self.kwargs and not data.get('room'):
            data['room'] = Room.objects.get(id=self.kwargs['room_pk'])
        elif not data.get('room'):
            raise serializers.ValidationError({"room": "Room is required."})

        try:
            layout = create_layout(
                tenant=tenant,
                room=data['room'],
                name=data['name'],
                grid_rows=data['grid_rows'],
                grid_cols=data['grid_cols'],
                spots_data=data.get('spots', [])
            )
        except DjangoValidationError as e:
            detail = e.message_dict if hasattr(e, 'message_dict') else (e.messages if hasattr(e, 'messages') else str(e))
            raise ValidationError(detail)

        layout = self.get_queryset().get(id=layout.id)
        read_serializer = RoomLayoutSerializer(layout)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        name = request.data.get('name', instance.name if not partial else None)
        grid_rows = request.data.get('grid_rows', instance.grid_rows if not partial else None)
        grid_cols = request.data.get('grid_cols', instance.grid_cols if not partial else None)
        spots_data = request.data.get('spots', None)

        try:
            update_layout(
                layout=instance,
                name=name,
                grid_rows=grid_rows,
                grid_cols=grid_cols,
                spots_data=spots_data
            )
        except DjangoValidationError as e:
            detail = e.message_dict if hasattr(e, 'message_dict') else (e.messages if hasattr(e, 'messages') else str(e))
            raise ValidationError(detail)

        layout = self.get_queryset().get(id=instance.id)
        read_serializer = RoomLayoutSerializer(layout)
        return Response(read_serializer.data)

    @action(detail=True, methods=['post'], permission_classes=[IsOwnerOrManager])
    def restore(self, request, pk=None):
        layout = RoomLayout.all_objects.get(id=pk)
        layout.restore()
        return Response(RoomLayoutSerializer(layout).data)

    @action(detail=True, methods=['get'])
    def classes(self, request, pk=None):
        layout = self.get_object()
        sessions = ClassSession.objects.filter(
            layout=layout,
            start_at__gte=timezone.now()
        ).select_related('template', 'room', 'staff')
        return Response(ClassSessionSerializer(sessions, many=True, context={'request': request}).data)


class StaffLocationViewSet(viewsets.ModelViewSet):
    queryset = StaffLocation.all_objects.all()
    serializer_class = StaffLocationSerializer
    permission_classes = [IsOwnerOrManager]

    def get_queryset(self):
        return StaffLocation.objects.select_related('staff', 'staff__profile', 'location')


class StaffAvailabilityViewSet(viewsets.ModelViewSet):
    queryset = StaffAvailability.all_objects.all()
    serializer_class = StaffAvailabilitySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = StaffAvailability.objects.select_related('staff', 'staff__profile')
        staff_id = self.request.query_params.get('staff')
        if staff_id:
            qs = qs.filter(staff_id=staff_id)
        return qs


class ClassTemplateViewSet(viewsets.ModelViewSet):
    queryset = ClassTemplate.all_objects.all()
    serializer_class = ClassTemplateSerializer
    permission_classes = [IsOwnerOrManager]

    def get_queryset(self):
        return ClassTemplate.objects.select_related('location')


class RecurrenceRuleViewSet(viewsets.ModelViewSet):
    queryset = RecurrenceRule.all_objects.all()
    serializer_class = RecurrenceRuleSerializer
    permission_classes = [IsOwnerOrManager]

    def get_queryset(self):
        return RecurrenceRule.objects.select_related('template', 'room', 'staff', 'staff__profile')

    @transaction.atomic
    def perform_create(self, serializer):
        rule = serializer.save()
        
        # Expand RecurrenceRule into ClassSession rows
        start_date = rule.start_date
        end_date = rule.end_date
        days_of_week = [d.lower() for d in rule.days_of_week]
        
        now = timezone.now()
        current_date = start_date
        sessions_to_create = []

        while current_date <= end_date:
            weekday_name = current_date.strftime('%A').lower()
            if weekday_name in days_of_week:
                naive_start = datetime.combine(current_date, rule.start_time)
                start_at = timezone.make_aware(naive_start, datetime_timezone.utc)
                end_at = start_at + timedelta(minutes=rule.template.duration_min)
                initial_status = 'completed' if end_at <= now else 'scheduled'

                # Check conflict
                # Room Conflict check
                if rule.room:
                    room_conflict = ClassSession.objects.filter(
                        room=rule.room,
                        start_at__lt=end_at,
                        end_at__gt=start_at,
                    ).exclude(status='cancelled').exists()
                    if room_conflict:
                        raise ValidationError(f"Room conflict detected for {rule.room} on {current_date} at {rule.start_time}")

                # Staff Conflict check
                if rule.staff:
                    staff_conflict = ClassSession.objects.filter(
                        staff=rule.staff,
                        start_at__lt=end_at,
                        end_at__gt=start_at,
                    ).exclude(status='cancelled').exists()
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
                        capacity=rule.template.default_capacity,
                        status=initial_status
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

    def list(self, request, *args, **kwargs):
        ClassSession.auto_complete_past_sessions()
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        ClassSession.auto_complete_past_sessions()
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        ClassSession.auto_complete_past_sessions()

        qs = ClassSession.objects.select_related(
            'template', 'template__location', 'room', 'staff', 'staff__profile'
        ).prefetch_related(
            'bookings', 'bookings__client', 'bookings__client__profile', 'waitlists'
        )
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
            if status_param.lower() in ['active', 'upcoming', 'scheduled']:
                qs = qs.filter(status='scheduled')
            elif status_param.lower() in ['past', 'completed', 'finished']:
                qs = qs.filter(status='completed')
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
    def perform_update(self, serializer):
        old_status = serializer.instance.status
        session = serializer.save()

        if session.status == 'cancelled' and old_status != 'cancelled':
            from .tasks import cancel_session_bookings_and_refund, process_credit_refund_job
            cancel_session_bookings_and_refund(session)
            try:
                process_credit_refund_job.delay(str(session.id))
            except Exception:
                pass

    @action(detail=True, methods=['post'], permission_classes=[IsOwnerOrManager])
    @transaction.atomic
    def cancel(self, request, pk=None):
        """
        Custom action to cancel an entire session, cancel all active bookings, and refund 1 credit to each client.
        """
        session = self.get_object()
        if session.status == 'cancelled':
            return Response({"detail": "Session is already cancelled."}, status=status.HTTP_400_BAD_REQUEST)

        session.status = 'cancelled'
        session.save()

        from .tasks import cancel_session_bookings_and_refund, process_credit_refund_job
        cancel_session_bookings_and_refund(session)
        try:
            process_credit_refund_job.delay(str(session.id))
        except Exception:
            pass

        return Response({"detail": "Session cancelled successfully and active booking credits refunded."}, status=status.HTTP_200_OK)

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        """
        Cancel a session. Triggers credit-refund for all active bookings and notifications.
        """
        session = self.get_object()
        session.status = 'cancelled'
        session.save()

        from .tasks import cancel_session_bookings_and_refund, process_credit_refund_job
        cancel_session_bookings_and_refund(session)
        try:
            process_credit_refund_job.delay(str(session.id))
        except Exception:
            pass

        return Response({"detail": "Session cancelled successfully and active booking credits refunded."}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'], url_path='spot-map')
    def spot_map(self, request, pk=None):
        """
        Glofox Member API:
        Returns complete real-time 2D layout matrix and computed spot states.
        State is server-computed: available | booked | blocked | mine | non_bookable.
        Optimized with 2 queries total (zero N+1) and O(1) in-memory lookups.
        """
        session = self.get_object()
        if not session.layout:
            return Response(
                {"error": "no_layout", "detail": "This class does not have an assigned room layout."},
                status=status.HTTP_404_NOT_FOUND
            )

        layout = session.layout
        # Single query 1: preload spots with their spot types
        spots = list(
            layout.spots.select_related('spot_type').order_by('row', 'col', 'number')
        )

        # Single query 2: preload only spot_id and client_id of active bookings for this session
        active_bookings = {
            str(spot_id): str(client_id)
            for spot_id, client_id in session.bookings.filter(status='booked', spot__isnull=False)
            .values_list('spot_id', 'client_id')
        }

        blocked_ids = {str(bid) for bid in (session.blocked_spots or [])}
        user_id_str = str(request.user.id) if request.user and request.user.is_authenticated else None

        my_spot_id = None
        spot_list = []
        for s in spots:
            spot_id_str = str(s.id)
            if not s.spot_type.is_bookable:
                state = 'non_bookable'
            elif s.is_blocked or spot_id_str in blocked_ids:
                state = 'blocked'
            elif spot_id_str in active_bookings:
                if user_id_str and active_bookings[spot_id_str] == user_id_str:
                    state = 'mine'
                    my_spot_id = spot_id_str
                else:
                    state = 'booked'
            else:
                state = 'available'

            spot_list.append({
                'id': str(s.id),
                'row': s.row,
                'col': s.col,
                'number': s.number,
                'label': s.label,
                'spot_type': str(s.spot_type.id),
                'spot_type_name': s.spot_type.name,
                'spot_type_prefix': s.spot_type.prefix,
                'color': s.spot_type.color,
                'is_bookable': s.spot_type.is_bookable,
                'state': state
            })

        return Response({
            'layout': {
                'id': str(layout.id),
                'name': layout.name,
                'grid_rows': layout.grid_rows,
                'grid_cols': layout.grid_cols,
                'capacity': layout.capacity,
                'version': session.layout_version or layout.version
            },
            'spots': spot_list,
            'my_spot': my_spot_id
        })

    @action(detail=True, methods=['post'], url_path='spots/block', permission_classes=[IsOwnerOrManager])
    def block_spot(self, request, pk=None):
        """
        Glofox Staff API:
        Blocks a specific spot for this class occurrence only (stored in session.blocked_spots).
        """
        session = self.get_object()
        spot_id = request.data.get('spot_id')
        if not spot_id:
            return Response({"detail": "spot_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        if not session.layout:
            return Response({"detail": "Session does not have a room layout."}, status=status.HTTP_400_BAD_REQUEST)

        if not session.layout.spots.filter(id=spot_id).exists():
            return Response({"detail": "Spot does not belong to this session's layout."}, status=status.HTTP_400_BAD_REQUEST)

        current_blocked = list(session.blocked_spots or [])
        if str(spot_id) not in [str(b) for b in current_blocked]:
            current_blocked.append(str(spot_id))
            session.blocked_spots = current_blocked
            session.save(update_fields=['blocked_spots'])

        return Response({
            "detail": "Spot blocked successfully for this occurrence.",
            "blocked_spots": session.blocked_spots
        })

    @action(detail=True, methods=['post'], url_path='spots/unblock', permission_classes=[IsOwnerOrManager])
    def unblock_spot(self, request, pk=None):
        """
        Glofox Staff API:
        Unblocks a previously blocked spot for this class occurrence.
        """
        session = self.get_object()
        spot_id = request.data.get('spot_id')
        if not spot_id:
            return Response({"detail": "spot_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        current_blocked = [str(b) for b in (session.blocked_spots or [])]
        if str(spot_id) in current_blocked:
            current_blocked.remove(str(spot_id))
            session.blocked_spots = current_blocked
            session.save(update_fields=['blocked_spots'])

        return Response({
            "detail": "Spot unblocked successfully.",
            "blocked_spots": session.blocked_spots
        })


class BookingViewSet(viewsets.ModelViewSet):
    queryset = Booking.all_objects.all()
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = [
        'client__email', 'client__first_name', 'client__last_name',
        'client__profile__first_name', 'client__profile__last_name', 'client__profile__nickname',
        'session__template__name', 'session__room__name', 'session__template__location__name',
        'session__staff__email', 'session__staff__first_name', 'session__staff__last_name',
        'session__staff__profile__first_name', 'session__staff__profile__last_name', 'session__staff__profile__nickname'
    ]
    ordering_fields = ['created_at', 'status', 'session__start_at', 'client__email']
    ordering = ['-created_at']

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        from apps.core.tenants.context import set_current_tenant, get_current_tenant
        tenant = getattr(request, 'tenant', None)
        if not tenant:
            header_tenant_id = (
                request.headers.get('X-Tenant-Id')
                or request.headers.get('X-Tenant-ID')
                or request.META.get('HTTP_X_TENANT_ID')
            )
            if header_tenant_id:
                try:
                    from apps.core.tenants.models import Tenant
                    tenant = Tenant.objects.filter(id=header_tenant_id).first()
                except Exception:
                    pass
        if not tenant and getattr(request.user, 'is_authenticated', False) and getattr(request.user, 'tenant', None):
            tenant = request.user.tenant
        if not tenant:
            tenant = get_current_tenant()
        if tenant:
            request.tenant = tenant
            set_current_tenant(tenant)

    def get_serializer_class(self):
        if self.action == 'create':
            return BookingCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return BookingEditSerializer
        return BookingReadSerializer

    def list(self, request, *args, **kwargs):
        ClassSession.auto_complete_past_sessions()
        Appointment.auto_complete_past_appointments()
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        ClassSession.auto_complete_past_sessions()
        Appointment.auto_complete_past_appointments()
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        ClassSession.auto_complete_past_sessions()
        Appointment.auto_complete_past_appointments()
        user = self.request.user

        # 1. Resolve active tenant for strict isolation and consistent cross-device synchronization
        tenant = getattr(self.request, 'tenant', None)
        if not tenant:
            header_tenant_id = (
                self.request.headers.get('X-Tenant-Id')
                or self.request.headers.get('X-Tenant-ID')
                or self.request.META.get('HTTP_X_TENANT_ID')
            )
            if header_tenant_id:
                try:
                    from apps.core.tenants.models import Tenant
                    tenant = Tenant.objects.filter(id=header_tenant_id).first()
                except Exception:
                    pass
        if not tenant and getattr(user, 'tenant', None):
            tenant = user.tenant
        if not tenant:
            from apps.core.tenants.context import get_current_tenant
            tenant = get_current_tenant()

        # 2. Build tenant-isolated base query
        # Ensures bookings created from Client App or Portal are always matched via booking.tenant OR session.tenant
        if tenant:
            base_qs = Booking.all_objects.filter(Q(tenant=tenant) | Q(session__tenant=tenant))
        elif user.is_superuser or getattr(user, 'role', None) == UserRole.PLATFORM_ADMIN:
            base_qs = Booking.all_objects.all()
        else:
            base_qs = Booking.objects.all()

        qs = base_qs.select_related(
            'session', 'session__template', 'session__template__location',
            'session__room', 'session__staff', 'session__staff__profile',
            'client', 'client__profile', 'credit_source'
        )

        # 3. Role-based scoping
        if user.role == UserRole.CLIENT:
            qs = qs.filter(client=user)
        elif user.role in [UserRole.GYM_MANAGER, UserRole.FRONT_DESK]:
            from apps.scheduling.models import StaffLocation
            assigned_locations = StaffLocation.all_objects.filter(staff=user).values_list('location_id', flat=True)
            if assigned_locations.exists():
                qs = qs.filter(session__template__location_id__in=assigned_locations).distinct()
        elif user.role == UserRole.TRAINER:
            from apps.scheduling.models import StaffLocation
            assigned_locations = StaffLocation.all_objects.filter(staff=user).values_list('location_id', flat=True)
            trainer_filter = Q(session__staff=user)
            if assigned_locations.exists():
                trainer_filter |= Q(session__template__location_id__in=assigned_locations)
            qs = qs.filter(trainer_filter).distinct()

        # 4. Search & Filter Options
        params = self.request.query_params

        # Client / User filter
        client_param = params.get('client') or params.get('client_id') or params.get('user') or params.get('user_id')
        if client_param:
            try:
                import uuid
                uuid.UUID(str(client_param))
                qs = qs.filter(client_id=client_param)
            except (ValueError, AttributeError):
                qs = qs.filter(
                    Q(client__email__icontains=client_param) |
                    Q(client__profile__first_name__icontains=client_param) |
                    Q(client__profile__last_name__icontains=client_param) |
                    Q(client__first_name__icontains=client_param) |
                    Q(client__last_name__icontains=client_param)
                )

        client_email = params.get('client_email') or params.get('email')
        if client_email:
            qs = qs.filter(client__email__icontains=client_email)

        # Session filter
        session_param = params.get('session') or params.get('session_id')
        if session_param:
            try:
                import uuid
                uuid.UUID(str(session_param))
                qs = qs.filter(session_id=session_param)
            except (ValueError, AttributeError):
                qs = qs.filter(session__template__name__icontains=session_param)

        session_name = params.get('session_name') or params.get('class_name')
        if session_name:
            qs = qs.filter(session__template__name__icontains=session_name)

        # Staff / Trainer filter
        staff_param = params.get('staff') or params.get('staff_id') or params.get('trainer') or params.get('trainer_id')
        if staff_param:
            try:
                import uuid
                uuid.UUID(str(staff_param))
                qs = qs.filter(session__staff_id=staff_param)
            except (ValueError, AttributeError):
                qs = qs.filter(
                    Q(session__staff__email__icontains=staff_param) |
                    Q(session__staff__profile__first_name__icontains=staff_param) |
                    Q(session__staff__profile__last_name__icontains=staff_param) |
                    Q(session__staff__first_name__icontains=staff_param) |
                    Q(session__staff__last_name__icontains=staff_param)
                )

        staff_name = params.get('staff_name') or params.get('trainer_name')
        if staff_name:
            qs = qs.filter(
                Q(session__staff__profile__first_name__icontains=staff_name) |
                Q(session__staff__profile__last_name__icontains=staff_name) |
                Q(session__staff__first_name__icontains=staff_name) |
                Q(session__staff__last_name__icontains=staff_name) |
                Q(session__staff__email__icontains=staff_name)
            )

        # Status filter
        status_param = params.get('status')
        if status_param and status_param.lower() != 'all':
            qs = qs.filter(status__iexact=status_param)

        # Location filter
        location_param = params.get('location') or params.get('location_id')
        if location_param:
            qs = qs.filter(session__template__location_id=location_param)

        # Date filters
        date_param = params.get('date')
        if date_param:
            from django.utils.dateparse import parse_date
            parsed = parse_date(date_param) or date_param[:10]
            qs = qs.filter(session__start_at__date=parsed)

        date_from = params.get('date_from') or params.get('start_date')
        if date_from:
            qs = qs.filter(session__start_at__gte=date_from)

        date_to = params.get('date_to') or params.get('end_date')
        if date_to:
            qs = qs.filter(session__start_at__lte=date_to)

        # 5. Latest-first sorting default across all pages
        ordering = self.request.query_params.get('ordering')
        if not ordering:
            qs = qs.order_by('-created_at')

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

        # Row lock session using all_objects to prevent isolation gaps
        session = ClassSession.all_objects.select_for_update().get(id=session_id)

        if session.status != 'scheduled':
            return Response({"detail": "Session is not scheduled."}, status=status.HTTP_400_BAD_REQUEST)

        # Set tenant context to session.tenant for consistent scoping
        if session.tenant:
            from apps.core.tenants.context import set_current_tenant
            set_current_tenant(session.tenant)
            request.tenant = session.tenant

        # Check existing booking
        existing_booking = Booking.all_objects.filter(client=target_client, session=session).first()
        if existing_booking and existing_booking.status in ['booked', 'checked_in', 'attended']:
            return Response({"detail": "Already booked this session."}, status=status.HTTP_400_BAD_REQUEST)

        # Check schedule conflict: client cannot book overlapping sessions or appointments
        client_session_conflict = Booking.all_objects.filter(
            client=target_client,
            session__start_at__lt=session.end_at,
            session__end_at__gt=session.start_at,
            status__in=['booked', 'checked_in', 'attended']
        ).exclude(session=session).exists()

        client_appt_conflict = Appointment.all_objects.filter(
            client=target_client,
            start_at__lt=session.end_at,
            end_at__gt=session.start_at,
            status__in=['scheduled', 'checked_in', 'attended']
        ).exists()

        if client_session_conflict or client_appt_conflict:
            return Response(
                {"detail": "Schedule conflict detected: you already have an active booking or appointment during this time slot."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Capacity Check
        current_bookings = session.bookings.filter(status__in=['booked', 'checked_in', 'attended']).count()
        if current_bookings >= session.capacity:
            return Response({"detail": "Session is full. Join waitlist instead."}, status=status.HTTP_400_BAD_REQUEST)

        # Spot Concurrency Check (if spot is selected)
        spot = serializer.validated_data.get('spot')
        if spot:
            # Row lock the specific spot to cleanly catch race conditions
            spot = Spot.all_objects.select_for_update().get(id=spot.id)
            is_spot_taken = session.bookings.filter(
                spot=spot,
                status='booked'
            ).exists()
            if is_spot_taken:
                return Response(
                    {"error": "spot_unavailable", "detail": "That spot was just taken. Please choose another spot."},
                    status=status.HTTP_409_CONFLICT
                )

        package = Package.all_objects.select_for_update().filter(
            client=target_client,
            credits_remaining__gt=0,
            expires_at__gt=timezone.now(),
            package_type__location=session.template.location
        ).first()

        if not package:
            has_other_packages = Package.all_objects.filter(
                client=target_client,
                credits_remaining__gt=0,
                expires_at__gt=timezone.now()
            ).exists()
            if has_other_packages:
                return Response(
                    {"detail": "Your purchased package is not valid for this location."},
                    status=status.HTTP_400_BAD_REQUEST
                )
            return Response(
                {"detail": "No active credits or packages found for booking."},
                status=status.HTTP_402_PAYMENT_REQUIRED
            )

        # Deduct Credit
        package.credits_remaining -= 1
        package.save()

        is_guest = serializer.validated_data.get('is_guest', False)

        # Determine fallback preferences from ClientBookingPreference
        client_pref = getattr(target_client, 'booking_preferences', None)
        default_join_mode = getattr(client_pref, 'join_mode', 'physical') if client_pref else 'physical'
        default_music = getattr(client_pref, 'music_preference', '') if client_pref else ''

        final_join_mode = serializer.validated_data.get('join_mode') or default_join_mode
        final_music_preference = serializer.validated_data.get('music_preference') or default_music

        booking_tenant = (
            session.tenant
            or getattr(request, 'tenant', None)
            or getattr(target_client, 'tenant', None)
            or get_current_tenant()
        )

        if existing_booking:
            # Reactivate existing booking row to satisfy unique_together constraint
            existing_booking.status = 'booked'
            existing_booking.credit_source = package
            existing_booking.spot = spot
            existing_booking.is_guest = is_guest
            existing_booking.join_mode = final_join_mode
            existing_booking.music_preference = final_music_preference
            if not existing_booking.tenant_id and booking_tenant:
                existing_booking.tenant = booking_tenant
            existing_booking.save()
            booking = existing_booking
        else:
            # Create Booking
            booking = Booking.all_objects.create(
                tenant=booking_tenant,
                client=target_client,
                session=session,
                spot=spot,
                is_guest=is_guest,
                credit_source=package,
                status='booked',
                join_mode=final_join_mode,
                music_preference=final_music_preference
            )

        # Create confirmation notification
        from apps.notifications.services import NotificationService
        from apps.notifications.events import BookingConfirmedEvent
        tenant_id = getattr(booking_tenant, 'id', None) or getattr(getattr(request, 'tenant', None), 'id', None)
        gym_name = getattr(booking_tenant, 'name', '') or getattr(getattr(request, 'tenant', None), 'name', 'Your Gym')
        if tenant_id:
            try:
                NotificationService.handle_event(BookingConfirmedEvent(
                    tenant_id=tenant_id,
                    recipient_id=target_client.id,
                    entity_id=booking.id,
                    context_data={
                        'client_name': target_client.profile.first_name if hasattr(target_client, 'profile') else target_client.email,
                        'class_name': session.template.name,
                        'class_time': str(session.start_at),
                        'gym_name': gym_name,
                    }
                ))
            except Exception:
                pass

        # Emit Rewards Event
        try:
            from apps.rewards.events import RewardEvent
            from apps.rewards.services import RewardEngineService
            template = getattr(session, 'template', None)
            RewardEngineService.handle_event(RewardEvent.create_booking_created(
                tenant_id=booking.tenant_id,
                user_id=booking.client_id,
                booking_id=booking.id,
                class_name=template.name if template else "",
                category=template.category if template else ""
            ))
        except Exception:
            pass

        # Trigger auto-assignment for unseated/guest bookings
        if booking.status == 'booked' and not booking.spot_id:
            from apps.scheduling.tasks import auto_assign_guest_bookings
            transaction.on_commit(lambda: auto_assign_guest_bookings.delay(str(session.id)))

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

        cutoff_hours = policy.cutoff_hours if policy else 12
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

        # Emit Rewards Event
        try:
            from apps.rewards.events import RewardEvent
            from apps.rewards.services import RewardEngineService
            RewardEngineService.handle_event(RewardEvent.create_booking_cancelled(
                tenant_id=booking.tenant_id,
                user_id=booking.client_id,
                booking_id=booking.id,
                is_late_cancel=not is_early_cancel
            ))
        except Exception:
            pass

        return Response({
            "status": "cancelled",
            "refunded": is_early_cancel,
            "detail": "Booking cancelled."
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='check-in', permission_classes=[IsAuthenticated])
    def check_in(self, request, pk=None):
        booking = self.get_object()
        user = request.user
        
        # Verify if user is staff OR the owner of the booking
        is_staff = user.role in [UserRole.PLATFORM_ADMIN, UserRole.GYM_OWNER, UserRole.GYM_MANAGER, UserRole.FRONT_DESK, UserRole.TRAINER]
        if not is_staff and booking.client != user:
            return Response({"detail": "You do not have permission to check in for this booking."}, status=status.HTTP_403_FORBIDDEN)

        booking.checked_in_at = timezone.now()
        booking.status = 'checked_in'
        booking.save()

        # Emit Rewards Event
        try:
            from apps.rewards.events import RewardEvent
            from apps.rewards.services import RewardEngineService
            
            template = getattr(booking.session, 'template', None)
            RewardEngineService.handle_event(RewardEvent.create_booking_attended(
                tenant_id=booking.tenant_id,
                user_id=booking.client_id,
                booking_id=booking.id,
                session_id=booking.session_id,
                class_name=template.name if template else "",
                category=template.category if template else "",
                intensity=template.intensity if template else "",
                occurred_at=booking.checked_in_at
            ))
        except Exception as e:
            # Reward failures must never break core check-in flow
            pass

        return Response({"detail": "Checked in successfully."})

    @action(detail=True, methods=['post'], url_path='check_in', permission_classes=[IsAuthenticated])
    def check_in_underscore(self, request, pk=None):
        return self.check_in(request, pk)

    @action(detail=True, methods=['post'], url_path='check-out', permission_classes=[IsAuthenticated])
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

    @action(detail=True, methods=['post'], url_path='check_out', permission_classes=[IsAuthenticated])
    def check_out_underscore(self, request, pk=None):
        return self.check_out(request, pk)

    @action(detail=True, methods=['patch'], url_path='spot', permission_classes=[IsAuthenticated])
    def change_spot(self, request, pk=None):
        """
        Glofox Member API:
        Change reserved spot before class start.
        Enforces atomic validation, spot existence, and availability.
        """
        booking = self.get_object()
        if request.user.role == UserRole.CLIENT and booking.client != request.user:
            return Response({"detail": "You do not have permission to change spot for another client's booking."}, status=status.HTTP_403_FORBIDDEN)

        serializer = BookingChangeSpotSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_spot_id = serializer.validated_data['spot_id']

        try:
            updated_booking = change_booking_spot(booking=booking, new_spot_id=new_spot_id)
        except SpotUnavailableError as e:
            return Response({"error": "spot_unavailable", "detail": str(e)}, status=status.HTTP_409_CONFLICT)
        except (ValidationError, DjangoValidationError) as e:
            detail = e.message_dict if hasattr(e, 'message_dict') else (e.messages if hasattr(e, 'messages') else str(e))
            return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)

        return Response(BookingReadSerializer(updated_booking, context={'request': request}).data)


class WaitlistViewSet(viewsets.ModelViewSet):
    queryset = Waitlist.all_objects.all()
    serializer_class = WaitlistSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = Waitlist.objects.select_related('client', 'client__profile', 'session', 'session__template')
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

    def list(self, request, *args, **kwargs):
        Appointment.auto_complete_past_appointments()
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        Appointment.auto_complete_past_appointments()
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        Appointment.auto_complete_past_appointments()

        user = self.request.user
        qs = Appointment.objects.select_related('provider', 'provider__profile', 'client', 'client__profile', 'location', 'room')
        if user.role == UserRole.CLIENT:
            qs = qs.filter(client=user)
        elif user.role in [UserRole.GYM_MANAGER, UserRole.FRONT_DESK]:
            qs = qs.filter(location__location_staff__staff=user).distinct()
        elif user.role == UserRole.TRAINER:
            qs = qs.filter(Q(provider=user) | Q(location__location_staff__staff=user)).distinct()
        
        status_param = self.request.query_params.get('status')
        if status_param and status_param.lower() != 'all':
            if status_param.lower() in ['active', 'upcoming', 'scheduled']:
                qs = qs.filter(status='scheduled')
            elif status_param.lower() in ['past', 'completed', 'finished']:
                qs = qs.filter(status='completed')
            else:
                qs = qs.filter(status__iexact=status_param)

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
                    ).exclude(status='cancelled').exists()

                    overlap_session = ClassSession.objects.filter(
                        staff=provider,
                        start_at__lt=slot_end,
                        end_at__gt=slot_start,
                    ).exclude(status='cancelled').exists()

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
        ).exclude(status='cancelled').exists()

        overlap_session = ClassSession.objects.filter(
            staff=provider,
            start_at__lt=end_at,
            end_at__gt=start_at,
        ).exclude(status='cancelled').exists()

        if overlap_appt or overlap_session:
            return Response({"detail": "Provider is not available during this time slot."}, status=status.HTTP_400_BAD_REQUEST)

        # Check client conflict
        client_appt_conflict = Appointment.objects.filter(
            client=target_client,
            start_at__lt=end_at,
            end_at__gt=start_at,
            status__in=['scheduled', 'checked_in', 'attended']
        ).exists()

        client_session_conflict = Booking.objects.filter(
            client=target_client,
            session__start_at__lt=end_at,
            session__end_at__gt=start_at,
            status__in=['booked', 'checked_in', 'attended']
        ).exists()

        if client_appt_conflict or client_session_conflict:
            return Response(
                {"detail": "Schedule conflict detected: you already have an active booking or appointment during this time slot."},
                status=status.HTTP_400_BAD_REQUEST
            )

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
        return SubstituteRequest.objects.select_related(
            'session', 'session__template', 'session__template__location',
            'session__room', 'session__staff', 'session__staff__profile',
            'requested_by_staff', 'requested_by_staff__profile',
            'accepted_by_staff', 'accepted_by_staff__profile'
        )

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

        # Check staff conflict for target_trainer
        session = sub_req.session
        staff_conflict = ClassSession.objects.filter(
            staff=target_trainer,
            start_at__lt=session.end_at,
            end_at__gt=session.start_at,
        ).exclude(status='cancelled').exclude(id=session.id).exists()

        appointment_conflict = Appointment.objects.filter(
            provider=target_trainer,
            start_at__lt=session.end_at,
            end_at__gt=session.start_at,
        ).exclude(status='cancelled').exists()

        if staff_conflict or appointment_conflict:
            return Response(
                {"detail": "This staff member is already assigned to another session at the same time slot."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Update substitute request
        sub_req.status = 'filled'
        sub_req.accepted_by_staff = target_trainer
        sub_req.save()

        # Update session
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

    @action(detail=False, methods=['get'], url_path='stats', permission_classes=[IsOwnerOrManager])
    def stats(self, request):
        from django.db.models import Sum
        from apps.scheduling.models import Package, PackageType

        active_packages = Package.objects.filter(status='active')
        total_subscribers = active_packages.values('client').distinct().count()

        from django.db.models.functions import Coalesce
        import decimal
        monthly_revenue = active_packages.aggregate(
            val=Sum(Coalesce('price', 'package_type__price'))
        )['val'] or decimal.Decimal('0.00')

        packages_stats = []
        from django.db.models import Count
        package_types = PackageType.objects.annotate(
            active_subscribers=Count('purchased_packages', filter=Q(purchased_packages__status='active'))
        )
        for pt in package_types:
            packages_stats.append({
                "package_type_id": str(pt.id),
                "package_name": pt.name,
                "price": str(pt.price),
                "subscriber_count": pt.active_subscribers
            })

        return Response({
            "total_subscribers": total_subscribers,
            "monthly_revenue": f"{monthly_revenue:.2f}",
            "subscribers_per_package": packages_stats
        }, status=status.HTTP_200_OK)

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

    @transaction.atomic
    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)

    def get_permissions(self):
        if self.action == 'my_active_packages':
            return [IsAuthenticated()]
        return super().get_permissions()

    def get_queryset(self):
        qs = Package.objects.select_related(
            'package_type', 'package_type__location', 'client', 'client__profile', 'assigned_by'
        )
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
        ).select_related('package_type', 'package_type__location', 'client', 'client__profile', 'assigned_by')
        return Response(PackageSerializer(packages, many=True).data)


class ReportsView(APIView):
    permission_classes = [IsFrontDeskOrAdmin]

    def get(self, request):
        report_type = request.query_params.get('type')
        location_id = request.query_params.get('location')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        if not report_type:
            return Response({"detail": "type query param is required (fill-rate, no-show, staff-utilization, pricing)."}, status=400)

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

        elif report_type == 'pricing' or report_type == 'packages':
            from django.db.models import Sum
            from apps.scheduling.models import Package, PackageType

            active_packages = Package.objects.filter(status='active')
            total_subscribers = active_packages.values('client').distinct().count()

            from django.db.models.functions import Coalesce
            import decimal
            monthly_revenue = active_packages.aggregate(
                val=Sum(Coalesce('price', 'package_type__price'))
            )['val'] or decimal.Decimal('0.00')

            packages_stats = []
            package_types = PackageType.objects.all()
            for pt in package_types:
                count = pt.purchased_packages.filter(status='active').count()
                packages_stats.append({
                    "package_type_id": str(pt.id),
                    "package_name": pt.name,
                    "price": str(pt.price),
                    "subscriber_count": count
                })

            return Response({
                "total_subscribers": total_subscribers,
                "monthly_revenue": f"{monthly_revenue:.2f}",
                "subscribers_per_package": packages_stats
            }, status=status.HTTP_200_OK)

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
        
        qs = User.objects.filter(role='client').select_related('profile')
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
        qs = self.queryset.select_related('client', 'client__profile', 'location')
        user = self.request.user
        if getattr(user, 'role', None) in [UserRole.GYM_MANAGER, UserRole.FRONT_DESK]:
            qs = qs.filter(location__location_staff__staff=user).distinct()
        return qs

    def perform_create(self, serializer):
        access_log = serializer.save()
        try:
            from apps.rewards.events import RewardEvent
            from apps.rewards.services import RewardEngineService
            RewardEngineService.handle_event(RewardEvent.create_facility_access(
                tenant_id=access_log.tenant_id,
                user_id=access_log.client_id,
                location_id=access_log.location_id,
                access_point=getattr(access_log, 'access_point', 'main_turnstile')
            ))
        except Exception:
            pass

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


class ClientBookingPreferenceView(APIView):
    """
    CRUD API for Client's Booking Preferences:
    - Join Mode / Remote Session ('physical', 'remote', etc.)
    - Virtual Coach ('virtual', 'in_person', etc.)
    - Music Preference ('Chill Hop', 'Upbeat EDM', 'Rock', etc.)

    Identifies client via request.user from auth token.
    Returns default values ('physical', '', '') if client has no preferences yet.
    Settings persist across login/logout.
    """
    permission_classes = [IsAuthenticated]

    DEFAULT_PREFERENCES = {
        'join_mode': 'physical',
        'remote_session': 'physical',
        'virtual_coach': '',
        'music_preference': '',
    }

    def get(self, request):
        from .models import ClientBookingPreference
        from .serializers import ClientBookingPreferenceSerializer

        preference = ClientBookingPreference.all_objects.filter(client=request.user).first()
        if not preference:
            data = {
                'id': None,
                'client': str(request.user.id),
                'client_email': request.user.email,
                **self.DEFAULT_PREFERENCES
            }
            return Response(data, status=status.HTTP_200_OK)

        serializer = ClientBookingPreferenceSerializer(preference)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        return self._save_preferences(request, partial=True, is_create=True)

    def put(self, request):
        return self._save_preferences(request, partial=False, is_create=False)

    def patch(self, request):
        return self._save_preferences(request, partial=True, is_create=False)

    def delete(self, request):
        from .models import ClientBookingPreference
        preference = ClientBookingPreference.all_objects.filter(client=request.user).first()
        if preference:
            preference.delete()
        return Response({'detail': 'Booking preferences reset to defaults.'}, status=status.HTTP_204_NO_CONTENT)

    def _save_preferences(self, request, partial=True, is_create=False):
        from .models import ClientBookingPreference
        from .serializers import ClientBookingPreferenceSerializer

        preference = ClientBookingPreference.all_objects.filter(client=request.user).first()
        created = False
        if not preference:
            tenant = getattr(request, 'tenant', None) or getattr(request.user, 'tenant', None)
            preference = ClientBookingPreference(
                client=request.user,
                tenant=tenant,
                join_mode='physical',
                virtual_coach='',
                music_preference=''
            )
            created = True

        serializer = ClientBookingPreferenceSerializer(preference, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        status_code = status.HTTP_201_CREATED if (created and is_create) else status.HTTP_200_OK
        return Response(serializer.data, status=status_code)

