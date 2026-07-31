"""
User Views
"""
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.core.exceptions import ValidationError
from apps.scheduling.permissions import IsOwnerOrManager

from apps.users.serializers import (
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    RegistrationInitSerializer,
    UserSerializer, 
    CreateUserSerializer, 
    UserProfileSerializer,
    VerifyOTPSerializer,
    ClientDetailedSchedulingSerializer,
    StaffDetailedSchedulingSerializer
)
from apps.users.services import AuthService, UserService
from apps.users.models import OTPPurpose, UserRole
from apps.core.permissions import TenantFeaturePermission
from rest_framework_simplejwt.views import TokenObtainPairView 
from rest_framework.views import APIView 
from rest_framework import parsers
from .models import User
class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing users within a tenant.
    """

    queryset = User.objects.all()
    
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]

    def get_queryset(self):
        # Enforce Tenant Isolation via Manager
        # Or explicitly filter if using standard objects manager
        return self.request.user.tenant.users.select_related('profile').all()

    @action(detail=False, methods=['post'], permission_classes=[IsOwnerOrManager])
    def create_staff(self, request):
        """
        Endpoint to create a Trainer or Manager.
        Only accessible by existing Gym Owners/Admins.
        """
        input_serializer = CreateUserSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        
        data = input_serializer.validated_data
        
        # Security Check: Ensure a gym owner can't create a Platform Admin
        if data['role'] == UserRole.PLATFORM_ADMIN:
             return Response(
                 {"detail": "Cannot create Platform Admin from this endpoint."}, 
                 status=status.HTTP_403_FORBIDDEN
             )

        try:
            # Delegate to Service - pass all profile fields
            new_user = UserService.create_user_with_profile(
                email=data['email'],
                password=data['password'],
                role=data['role'],
                tenant=request.tenant, # Injected by TenantMiddleware
                # Basic Profile
                nickname=data.get('nickname'),
                bio=data.get('bio'),
                profile_image=data.get('profile_image'),
                # Personal Information
                first_name=data.get('first_name'),
                last_name=data.get('last_name'),
                phone_number=data.get('phone_number'),
                date_of_birth=data.get('date_of_birth'),
                gender=data.get('gender'),
                height=data.get('height'),
                weight=data.get('weight'),
                # Address Information
                address=data.get('address'),
                city=data.get('city'),
                country=data.get('country'),
                postal_code=data.get('postal_code'),
                # Emergency Contact
                emergency_contact_name=data.get('emergency_contact_name'),
                emergency_contact_phone=data.get('emergency_contact_phone'),
            )
            
            # Serialize Output
            output_serializer = UserSerializer(new_user)
            return Response(output_serializer.data, status=status.HTTP_201_CREATED)
            
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get', 'patch'])
    def me(self, request):
        """
        GET: Retrieve current profile.
        PATCH: Optimized partial update for current profile.
        """
        user = request.user
        
        if request.method == 'PATCH':
            # Use partial=True to allow only updating specific fields (e.g., just bio)
            serializer = self.get_serializer(user, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)

        serializer = self.get_serializer(user)
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        """
        Standard DELETE /api/profiles/{id}/
        Restricted to Owners/Managers via permission_classes.
        """
        user_to_delete = self.get_object()
        
        try:
            UserService.delete_user(user_to_delete)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path='clients-detailed-scheduling', permission_classes=[IsOwnerOrManager])
    def clients_detailed_scheduling(self, request):
        """
        GET: Paginated list of clients with complete scheduling, packages, check-in history.
        """
        # Filter for clients under the owner's active tenant
        queryset = self.request.user.tenant.users.filter(role=UserRole.CLIENT).select_related('profile')
        
        # Filter by specific ID if provided in query params
        user_id = request.query_params.get('id')
        if user_id:
            queryset = queryset.filter(id=user_id)
        
        # Support search
        search_query = request.query_params.get('search')
        if search_query:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(email__icontains=search_query) |
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(profile__nickname__icontains=search_query) |
                Q(profile__phone_number__icontains=search_query)
            )

        # Optimize queries by prefetching related data for the paginated page.
        # Pagination must happen first, so we only fetch related data for the active page!
        paginator = ClientDetailedSchedulingPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        
        if page is not None:
            user_ids = [u.id for u in page]
            
            from apps.scheduling.models import Booking, Appointment, Package, FacilityAccessLog, Waitlist
            
            # Fetch all matching packages
            packages = Package.objects.filter(client_id__in=user_ids).select_related('package_type')
            packages_map = {}
            for p in packages:
                packages_map.setdefault(p.client_id, []).append(p)
                
            # Fetch all bookings
            bookings = Booking.objects.filter(client_id__in=user_ids).select_related(
                'session', 'session__template', 'session__template__location', 'session__room', 'session__staff', 'session__staff__profile'
            )
            bookings_map = {}
            for b in bookings:
                bookings_map.setdefault(b.client_id, []).append(b)
                
            # Fetch all appointments
            appointments = Appointment.objects.filter(client_id__in=user_ids).select_related(
                'provider', 'provider__profile', 'location', 'room'
            )
            appointments_map = {}
            for a in appointments:
                appointments_map.setdefault(a.client_id, []).append(a)
                
            # Fetch all logs
            logs = FacilityAccessLog.objects.filter(client_id__in=user_ids).select_related('location')
            logs_map = {}
            for log in logs:
                logs_map.setdefault(log.client_id, []).append(log)
                
            # Fetch waitlists
            waitlists = Waitlist.objects.filter(client_id__in=user_ids).select_related('session', 'session__template')
            waitlists_map = {}
            for w in waitlists:
                waitlists_map.setdefault(w.client_id, []).append(w)
                
            # Attach lists to the user objects in memory for serializers to pick up
            for u in page:
                u.prefetched_bookings = bookings_map.get(u.id, [])
                u.prefetched_packages = packages_map.get(u.id, [])
                u.prefetched_appointments = appointments_map.get(u.id, [])
                u.prefetched_logs = logs_map.get(u.id, [])
                u.prefetched_waitlists = waitlists_map.get(u.id, [])
                
            serializer = ClientDetailedSchedulingSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
            
        serializer = ClientDetailedSchedulingSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='staff-detailed-scheduling', permission_classes=[IsOwnerOrManager])
    def staff_detailed_scheduling(self, request):
        """
        GET: Paginated list of staff members with complete scheduling, locations, availabilities.
        """
        # Filter for staff under the owner's active tenant (role != client)
        queryset = self.request.user.tenant.users.exclude(role=UserRole.CLIENT).select_related('profile')
        
        # Filter by specific ID if provided in query params
        user_id = request.query_params.get('id')
        if user_id:
            queryset = queryset.filter(id=user_id)
        
        # Support search
        search_query = request.query_params.get('search')
        if search_query:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(email__icontains=search_query) |
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(profile__nickname__icontains=search_query) |
                Q(profile__phone_number__icontains=search_query)
            )

        # Optimize queries by prefetching related data for the paginated page.
        paginator = StaffDetailedSchedulingPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        
        if page is not None:
            user_ids = [u.id for u in page]
            
            from apps.scheduling.models import StaffLocation, StaffAvailability, ClassSession, Appointment, StaffClientAssignment, SubstituteRequest
            
            # Fetch all staff location mappings
            staff_locs = StaffLocation.objects.filter(staff_id__in=user_ids).select_related('location')
            staff_locs_map = {}
            for sl in staff_locs:
                staff_locs_map.setdefault(sl.staff_id, []).append(sl)
                
            # Fetch availabilities
            avails = StaffAvailability.objects.filter(staff_id__in=user_ids)
            avails_map = {}
            for a in avails:
                avails_map.setdefault(a.staff_id, []).append(a)
                
            # Fetch class sessions
            sessions = ClassSession.objects.filter(staff_id__in=user_ids).select_related('template', 'template__location', 'room')
            sessions_map = {}
            for s in sessions:
                sessions_map.setdefault(s.staff_id, []).append(s)
                
            # Fetch appointments
            appts = Appointment.objects.filter(provider_id__in=user_ids).select_related('client', 'client__profile', 'location', 'room')
            appts_map = {}
            for a in appts:
                appts_map.setdefault(a.provider_id, []).append(a)
                
            # Fetch client assignments
            clients = StaffClientAssignment.objects.filter(staff_id__in=user_ids).select_related('client', 'client__profile')
            clients_map = {}
            for c in clients:
                clients_map.setdefault(c.staff_id, []).append(c)
                
            # Fetch raised subs
            raised_subs = SubstituteRequest.objects.filter(requested_by_staff_id__in=user_ids).select_related('session', 'session__template', 'accepted_by_staff')
            raised_subs_map = {}
            for sr in raised_subs:
                raised_subs_map.setdefault(sr.requested_by_staff_id, []).append(sr)
                
            # Fetch accepted subs
            accepted_subs = SubstituteRequest.objects.filter(accepted_by_staff_id__in=user_ids).select_related('session', 'session__template', 'requested_by_staff')
            accepted_subs_map = {}
            for sr in accepted_subs:
                accepted_subs_map.setdefault(sr.accepted_by_staff_id, []).append(sr)
                
            # Attach lists in memory
            for u in page:
                u.prefetched_staff_locations = staff_locs_map.get(u.id, [])
                u.prefetched_availabilities = avails_map.get(u.id, [])
                u.prefetched_sessions = sessions_map.get(u.id, [])
                u.prefetched_provider_appointments = appts_map.get(u.id, [])
                u.prefetched_assigned_clients = clients_map.get(u.id, [])
                u.prefetched_raised_subs = raised_subs_map.get(u.id, [])
                u.prefetched_accepted_subs = accepted_subs_map.get(u.id, [])
                
            serializer = StaffDetailedSchedulingSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)
            
        serializer = StaffDetailedSchedulingSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='detailed-scheduling', permission_classes=[IsOwnerOrManager])
    def detailed_scheduling(self, request, pk=None):
        """
        GET: Complete detailed scheduling summary for a single client or staff member by ID.
        """
        user = self.get_object()
        
        # Optimize queries by prefetching relationships for this single user
        from apps.scheduling.models import Booking, Appointment, Package, FacilityAccessLog, Waitlist, StaffLocation, StaffAvailability, StaffClientAssignment, SubstituteRequest, ClassSession
        
        if user.role == UserRole.CLIENT:
            user.prefetched_bookings = list(Booking.objects.filter(client=user).select_related(
                'session', 'session__template', 'session__template__location', 'session__room', 'session__staff', 'session__staff__profile'
            ))
            user.prefetched_packages = list(Package.objects.filter(client=user).select_related('package_type'))
            user.prefetched_appointments = list(Appointment.objects.filter(client=user).select_related(
                'provider', 'provider__profile', 'location', 'room'
            ))
            user.prefetched_logs = list(FacilityAccessLog.objects.filter(client=user).select_related('location'))
            user.prefetched_waitlists = list(Waitlist.objects.filter(client=user).select_related('session', 'session__template'))
            
            serializer = ClientDetailedSchedulingSerializer(user)
        else:
            user.prefetched_staff_locations = list(StaffLocation.objects.filter(staff=user).select_related('location'))
            user.prefetched_availabilities = list(StaffAvailability.objects.filter(staff=user))
            user.prefetched_sessions = list(ClassSession.objects.filter(staff=user).select_related('template', 'template__location', 'room'))
            user.prefetched_provider_appointments = list(Appointment.objects.filter(provider=user).select_related('client', 'client__profile', 'location', 'room'))
            user.prefetched_assigned_clients = list(StaffClientAssignment.objects.filter(staff=user).select_related('client', 'client__profile'))
            user.prefetched_raised_subs = list(SubstituteRequest.objects.filter(requested_by_staff=user).select_related('session', 'session__template', 'accepted_by_staff'))
            user.prefetched_accepted_subs = list(SubstituteRequest.objects.filter(accepted_by_staff=user).select_related('session', 'session__template', 'requested_by_staff'))
            
            serializer = StaffDetailedSchedulingSerializer(user)
            
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='deactivate', permission_classes=[IsOwnerOrManager])
    def deactivate(self, request, pk=None):
        """
        POST: Toggle deactivation/activation of a client or staff member.
        If deactivating and user is a client, cancel all active bookings and refund credits.
        """
        from django.db import transaction
        
        with transaction.atomic():
            user = self.get_object()
            
            if user.is_active:
                # Deactivate
                user.is_active = False
                user.save()
                
                from apps.scheduling.models import Booking, Package
                from apps.scheduling.tasks import process_waitlist_promotion_job
                
                active_bookings = Booking.objects.filter(client=user, status='booked').select_for_update()
                cancelled_count = active_bookings.count()
                
                for booking in active_bookings:
                    booking.status = 'cancelled'
                    booking.save()
                    
                    # Refund credit if a package was used
                    if booking.credit_source:
                        pkg = Package.objects.select_for_update().get(id=booking.credit_source.id)
                        pkg.credits_remaining += 1
                        pkg.save()
                    
                    # Trigger waitlist promotion
                    process_waitlist_promotion_job.delay(str(booking.session.id))
                    
                return Response({
                    "detail": "User deactivated successfully.",
                    "is_active": False,
                    "cancelled_bookings_count": cancelled_count
                }, status=status.HTTP_200_OK)
            else:
                # Reactivate
                user.is_active = True
                user.save()
                
                return Response({
                    "detail": "User activated successfully.",
                    "is_active": True,
                    "cancelled_bookings_count": 0
                }, status=status.HTTP_200_OK)

class ClientDetailedSchedulingPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class StaffDetailedSchedulingPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Login View: Returns JWT Access/Refresh tokens + User Profile Data.
    """
    serializer_class = CustomTokenObtainPairSerializer


class UserRegistrationView(APIView):
    permission_classes = [permissions.AllowAny]
    serializer_class = CreateUserSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data['role'] == UserRole.PLATFORM_ADMIN:
            return Response(
                {"detail": "Platform Admins cannot register publicly."}, 
                status=status.HTTP_403_FORBIDDEN
            )

        # 1. Resolve Tenant
        target_tenant = None
        
        # Priority A: Explicit ID (Mobile Apps / Central Frontend)
        if 'tenant_id' in data:
            from apps.core.tenants.models import Tenant
            try:
                target_tenant = Tenant.objects.get(id=data['tenant_id'])
            except Tenant.DoesNotExist:
                return Response(
                    {"detail": "Invalid tenant_id provided."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        # Priority B: Subdomain (Web Tenant)
        elif hasattr(request, 'tenant') and request.tenant:
            target_tenant = request.tenant

        # Validation: Regular users must belong to a tenant
        if not target_tenant and data['role'] != UserRole.GYM_OWNER:
             return Response(
                 {"detail": "Registration requires a valid Tenant Context (via subdomain or tenant_id)."}, 
                 status=status.HTTP_400_BAD_REQUEST
             )

        try:
            user = UserService.create_user_with_profile(
                email=data['email'],
                password=data['password'],
                role=data['role'],
                tenant=target_tenant,
                # Basic Profile
                nickname=data.get('nickname'),
                bio=data.get('bio'),
                profile_image=data.get('profile_image'),
                # Personal Information
                first_name=data.get('first_name'),
                last_name=data.get('last_name'),
                phone_number=data.get('phone_number'),
                date_of_birth=data.get('date_of_birth'),
                gender=data.get('gender'),
                height=data.get('height'),
                weight=data.get('weight'),
                # Address Information
                address=data.get('address'),
                city=data.get('city'),
                country=data.get('country'),
                postal_code=data.get('postal_code'),
                # Emergency Contact
                emergency_contact_name=data.get('emergency_contact_name'),
                emergency_contact_phone=data.get('emergency_contact_phone'),
            )
            
            return Response(
                UserSerializer(user).data, 
                status=status.HTTP_201_CREATED
            )
            
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

class RegistrationInitView(APIView):
    """
    Step 1: Init Registration.
    Accepts: Multipart/Form-Data (Email, Password, Tenant, Image).
    Action: Saves to Pending -> Sends OTP.
    """
    permission_classes = [permissions.AllowAny]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser] # Required for Image and JSON payloads
    serializer_class = RegistrationInitSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        # 1. Create Pending Record (Handles Image Save)
        AuthService.create_pending_registration(
            validated_data=serializer.validated_data,
            files=request.FILES
        )

        # 2. Generate & Send OTP
        email = serializer.validated_data['email']
        code = AuthService.create_email_otp(email, OTPPurpose.REGISTRATION)
        AuthService.send_email_otp(email, code, OTPPurpose.REGISTRATION)

        return Response(
            {"detail": "OTP sent to email. Verify to complete registration."},
            status=status.HTTP_200_OK
        )


class VerifyOTPAndRegisterView(APIView):
    """
    Step 2: Finalize Registration.
    Accepts: JSON (Email, Code).
    Action: Verifies OTP -> Creates User -> Returns Token/User.
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = VerifyOTPSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        
        # 1. Verify
        AuthService.verify_email_otp(
            email=data['email'], 
            code=data['code'], 
            purpose=OTPPurpose.REGISTRATION
        )
        
        # 2. Finalize (Move Pending -> Real User)
        user = AuthService.finalize_registration(email=data['email'])

        return Response(
            {
                "detail": "Registration successful.",
                "user": UserSerializer(user).data
            },
            status=status.HTTP_201_CREATED
        )

class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        AuthService.change_password(
            user=request.user, 
            new_password=serializer.validated_data['new_password']
        )
        
        return Response({"detail": "Password updated successfully."}, status=status.HTTP_200_OK)

from apps.users.serializers import ForgotPasswordInitSerializer, ForgotPasswordVerifySerializer

class ForgotPasswordInitView(APIView):
    """
    Initializes the forgot password flow by generating and emailing an OTP.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ForgotPasswordInitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        email = serializer.validated_data['email']
        
        # Generate OTP
        code = AuthService.create_email_otp(email=email, purpose=OTPPurpose.PASSWORD_RESET)
        
        # Send OTP
        AuthService.send_email_otp(email=email, code=code, purpose=OTPPurpose.PASSWORD_RESET)
        
        return Response({"detail": "Password reset code sent to your email."}, status=status.HTTP_200_OK)

class ForgotPasswordVerifyView(APIView):
    """
    Verifies the OTP and updates the user's password.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ForgotPasswordVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        data = serializer.validated_data
        email = data['email']
        
        # 1. Verify OTP
        AuthService.verify_email_otp(
            email=email, 
            code=data['code'], 
            purpose=OTPPurpose.PASSWORD_RESET
        )
        
        # 2. Update Password
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.get(email=email)
        
        AuthService.change_password(
            user=user, 
            new_password=data['new_password']
        )
        
        return Response({"detail": "Password has been successfully reset."}, status=status.HTTP_200_OK)
