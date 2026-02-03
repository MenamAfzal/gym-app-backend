"""
User Views
"""
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError
from apps.scheduling.permissions import IsOwnerOrManager

from apps.users.serializers import (
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    RegistrationInitSerializer,
    UserSerializer, 
    CreateUserSerializer, 
    UserProfileSerializer,
    VerifyOTPSerializer
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
            # Delegate to Service
            new_user = UserService.create_user_with_profile(
                email=data['email'],
                password=data['password'],
                role=data['role'],
                tenant=request.tenant, # Injected by TenantMiddleware
                nickname=data.get('nickname'),
                bio=data.get('bio'),
                profile_image=data.get('profile_image')
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
                nickname=data.get('nickname'),
                bio=data.get('bio'),
                profile_image=data.get('profile_image')
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
    parser_classes = [parsers.MultiPartParser, parsers.FormParser] # Required for Image
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
