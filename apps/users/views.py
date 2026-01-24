"""
User Views
"""
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.core.exceptions import ValidationError

from apps.users.serializers import (
    CustomTokenObtainPairSerializer,
    UserSerializer, 
    CreateUserSerializer, 
    UserProfileSerializer
)
from apps.users.services import UserService
from apps.users.models import UserRole
from apps.core.permissions import TenantFeaturePermission
from rest_framework_simplejwt.views import TokenObtainPairView 
from rest_framework.views import APIView 

class UserViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing users within a tenant.
    """
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Enforce Tenant Isolation via Manager
        # Or explicitly filter if using standard objects manager
        return self.request.user.tenant.users.select_related('profile').all()

    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAdminUser])
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
                bio=data.get('bio')
            )
            
            # Serialize Output
            output_serializer = UserSerializer(new_user)
            return Response(output_serializer.data, status=status.HTTP_201_CREATED)
            
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def me(self, request):
        """
        Get current logged-in user's profile.
        """
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Login View: Returns JWT Access/Refresh tokens + User Profile Data.
    """
    serializer_class = CustomTokenObtainPairSerializer


class UserRegistrationView(APIView):
    """
    Public Registration Endpoint.
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = CreateUserSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Security: Public registration usually implies 'Client' or 'Gym Owner' (via specific flow).
        # We must block 'Platform Admin' creation here.
        if data['role'] == UserRole.PLATFORM_ADMIN:
            return Response(
                {"detail": "Platform Admins cannot register publicly."}, 
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            # Note: For public registration, we might need to handle 'tenant' lookup 
            # if a user registers via a gym's subdomain.
            # For now, we assume the middleware has set request.tenant if applicable,
            # OR the payload must include a tenant_id if it's an invite flow.
            
            # Logic: If request.tenant exists (subdomain), register user to that tenant.
            target_tenant = getattr(request, 'tenant', None)
            
            # If no subdomain, and they try to register as Client/Trainer, fail.
            if not target_tenant and data['role'] != UserRole.GYM_OWNER:
                 return Response(
                     {"detail": "Must register via a Gym Subdomain."}, 
                     status=status.HTTP_400_BAD_REQUEST
                 )

            user = UserService.create_user_with_profile(
                email=data['email'],
                password=data['password'],
                role=data['role'],
                tenant=target_tenant,
                nickname=data.get('nickname'),
                bio=data.get('bio')
            )
            
            return Response(
                UserSerializer(user).data, 
                status=status.HTTP_201_CREATED
            )
            
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
            