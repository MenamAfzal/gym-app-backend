from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone


from .models import Session, Booking, StaffClientAssignment, PricingOption, ClientPass
from .serializers import (
    ClientPassSerializer, PricingOptionSerializer, SessionSerializer, BookingCreateSerializer, BookingReadSerializer, 
    BookingEditSerializer, StaffAssignClientSerializer
)
from .permissions import IsAuthenticated, IsGymStaffOrOwner, IsOwnerOrManager, IsAssignedClient
from apps.users.models import User, UserRole

from apps.scheduling import permissions

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class StaffAssignmentViewSet(viewsets.ModelViewSet):
    """
    Admin Only: Assign Clients to Staff.
    """
    queryset = StaffClientAssignment.objects.all()
    serializer_class = StaffAssignClientSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    
    def get_queryset(self):
        user = self.request.user
        # Start with the optimized select_related to prevent N+1 queries
        queryset = StaffClientAssignment.objects.all().select_related(
            'staff__profile', 
            'client__profile'
        )

        # 1. If the user is a Trainer, filter to show only their assignments
        if user.role == UserRole.TRAINER:
            return queryset.filter(staff=user)
        
        # 2. If the user is an Admin or Manager, return the full tenant-scoped list
        elif user.role in [UserRole.GYM_OWNER, UserRole.GYM_MANAGER]:
            return queryset

        # 3. Fallback: Return nothing for roles that shouldn't access this (like standard clients)
        return queryset.none()
    
    @action(detail=False, methods=['post'], url_path='bulk-assign')
    def bulk_assign(self, request):
        staff_id = request.data.get('staff')
        client_ids = request.data.get('clients', [])

        # 1. Validate Staff Exists and is a Trainer
        staff = get_object_or_404(User, id=staff_id, role='trainer', tenant=request.tenant)

        # 2. Validate all Client IDs exist and belong to this gym 
        valid_clients = User.objects.filter(
            id__in=client_ids, 
            role='client', 
            tenant=request.tenant
        ).values_list('id', flat=True)

        # Check if any IDs were invalid
        invalid_ids = set(client_ids) - set([str(cid) for cid in valid_clients])
        if invalid_ids:
            return Response(
                {"detail": f"The following Client IDs are invalid or belong to another gym: {list(invalid_ids)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # 3. Create Assignment Objects
        assignments = [
            StaffClientAssignment(
                staff=staff, 
                client_id=c_id, 
                tenant=request.tenant
            ) for c_id in valid_clients
        ]
        
        # 4. Perform Bulk Create
        # ignore_conflicts=True handles cases where the link already exists
        StaffClientAssignment.objects.bulk_create(assignments, ignore_conflicts=True)
        
        return Response({
            "detail": f"Successfully processed {len(valid_clients)} assignments."
        }, status=status.HTTP_201_CREATED)

class SessionViewSet(viewsets.ModelViewSet):
    """
    Manages Sessions.
    Admin: Full CRUD.
    Staff: Can see all (or filtered).
    Client: Can see all, but Booking logic restricts interaction.
    """
    serializer_class = SessionSerializer
    pagination_class = StandardResultsSetPagination
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ['start_time', 'session_type', 'staff']
    ordering_fields = ['start_time']

    def get_queryset(self):
        # Optimize: Fetch staff profile to avoid N+1 in serializer "staff_name"
        return Session.objects.all().select_related('staff', 'staff__profile')

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [IsOwnerOrManager()] # Only Admin creates sessions
        return [IsAuthenticated()] # Clients/Staff can view

class BookingViewSet(viewsets.ModelViewSet):
    """
    Handles Booking Logic with Atomic Transactions.
    """
    pagination_class = StandardResultsSetPagination
    
    def get_serializer_class(self):
        if self.action == 'create':
            return BookingCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return BookingEditSerializer
        return BookingReadSerializer

    def get_queryset(self):
        user = self.request.user
        qs = Booking.objects.select_related('session', 'session__staff', 'session__staff__profile')

        if user.role == UserRole.CLIENT:
            return qs.filter(client=user)
        elif user.role == UserRole.TRAINER:
            # Trainers see sessions they lead OR their assigned clients
            return qs.filter(
                Q(session__staff=user) | 
                Q(client__assigned_staff_relations__staff=user)
            ).distinct()
        
        return qs # Admin sees all

    def create(self, request, *args, **kwargs):
        """
        Handles Credit Deduction Atomically.
        Supports:
        1. Client self-booking.
        2. Staff booking for a specific client (via 'client_id').
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        session = serializer.validated_data['session']
        
        # --- LOGIC FIX: Determine the Target Client ---
        if request.user.role == UserRole.CLIENT:
            client = request.user
        else:
            # Staff/Admin must provide a client_id to book for someone else
            client_id = request.data.get('client_id')
            if not client_id:
                raise ValidationError({"client_id": "Staff must specify a client_id."})
            
            client = get_object_or_404(User, id=client_id, role=UserRole.CLIENT)
            
            # Staff Restriction: Can only book for assigned clients (unless Admin)
            if request.user.role == UserRole.TRAINER:
                 is_assigned = request.user.assigned_client_relations.filter(client=client).exists()
                 if not is_assigned:
                     raise PermissionDenied("You can only book for your assigned clients.")

        # --- Atomic Transaction Start ---
        with transaction.atomic():
            # 1. Find Valid Pass (Row Locking)
            active_pass = ClientPass.objects.select_for_update().filter(
                client=client,
                is_active=True,
                credits_remaining__gt=0,
                start_date__lte=session.start_time.date(),
                expiry_date__gte=session.start_time.date()
            ).first()

            if not active_pass:
                return Response(
                    {"detail": f"Client {client.email} has no active credits for this date."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # 2. Check Capacity (Double Check inside transaction)
            # Use select_for_update on session to prevent overbooking race condition
            session_lock = Session.objects.select_for_update().get(id=session.id)
            if session_lock.bookings.filter(status='booked').count() >= session_lock.capacity:
                 return Response({"detail": "Session is full."}, status=status.HTTP_400_BAD_REQUEST)

            # 3. Deduct Credit
            active_pass.credits_remaining -= 1
            active_pass.save()

            # 4. Create Booking
            booking = Booking.objects.create(
                client=client,
                used_pass=active_pass,
                **serializer.validated_data
            )

        headers = self.get_success_headers(serializer.data)
        return Response(
            BookingReadSerializer(booking).data, 
            status=status.HTTP_201_CREATED, 
            headers=headers
        )

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Custom endpoint to Cancel and Refund.
        """
        booking = self.get_object()

        if booking.status == 'cancelled':
            return Response({"detail": "Booking already cancelled"}, status=400)

        # Optional: Check cancellation window (e.g., 2 hours before)
        # if booking.session.start_time - timezone.now() < timedelta(hours=2):
        #    return Response({"detail": "Too late to cancel"}, status=400)

        with transaction.atomic():
            booking.status = 'cancelled'
            booking.save()

            if booking.used_pass:
                # Lock and Refund
                client_pass = ClientPass.objects.select_for_update().get(id=booking.used_pass.id)
                client_pass.credits_remaining += 1
                client_pass.save()

        return Response({"status": "cancelled", "refunded": True})

    def destroy(self, request, *args, **kwargs):
        """
        Disable hard deletes to protect financial history.
        Force users to use /cancel/ endpoint.
        """
        return Response(
            {"detail": "Please use the 'cancel' action to remove a booking."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )

class PricingOptionViewSet(viewsets.ModelViewSet):
    queryset = PricingOption.objects.all()
    serializer_class = PricingOptionSerializer # (Assume standard ModelSerializer)
    permission_classes = [IsOwnerOrManager]

    def perform_create(self, serializer):
        """
        Force the pricing option to belong to the current user's tenant.
        """
        # Ensure the tenant is pulled from the request (set by Middleware)
        serializer.save(tenant=self.request.tenant)

    def get_queryset(self):
        """
        Explicitly filter by the current tenant to be safe.
        """
        return PricingOption.objects.filter(tenant=self.request.tenant)    

class ClientPassViewSet(viewsets.ModelViewSet):
    queryset = ClientPass.objects.all()
    serializer_class = ClientPassSerializer # (Assume standard ModelSerializer)
    permission_classes = [IsOwnerOrManager] # Admin manually assigns passes for now
        