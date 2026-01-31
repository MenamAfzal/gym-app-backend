"""
User Serializers
"""
from rest_framework import serializers
from apps.users.models import User, UserProfile, UserRole, OTPPurpose
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from apps.core.tenants.models import Tenant

class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ['nickname', 'bio', 'profile_image']
        read_only_fields = ['id', 'created_at']

class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer()

    class Meta:
        model = User
        fields = ['id', 'email', 'role', 'profile', 'date_joined']
        read_only_fields = ['id', 'email', 'role', 'date_joined']

class CreateUserSerializer(serializers.Serializer):
    """
    Input serializer for creating a new user.
    """
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    role = serializers.ChoiceField(choices=UserRole.choices)
    nickname = serializers.CharField(required=False, allow_blank=True)
    bio = serializers.CharField(required=False, allow_blank=True)
    profile_image = serializers.ImageField(required=False, allow_null=True) 
    tenant_id = serializers.UUIDField(required=False)
    def validate(self, attrs):
        """
        Cross-field validation if necessary.
        """
        # Logic is handled in Service, but basic checks can go here.
        return attrs
    
class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Customizes the JWT response to include user details.
    """
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)

        # Add custom claims to the Access Token payload
        token['role'] = user.role
        token['email'] = user.email
        if user.tenant:
            token['tenant_id'] = str(user.tenant.id)
            token['tenant_name'] = user.tenant.name
        
        return token

    def validate(self, attrs):
        data = super().validate(attrs)

        # Add custom data to the Response Body
        data['user'] = {
            'id': str(self.user.id),
            'email': self.user.email,
            'role': self.user.role,
            'nickname': self.user.profile.nickname if hasattr(self.user, 'profile') else "",
            # Helper for frontend routing
            'is_platform_admin': self.user.tenant is None
        }
        
        if self.user.tenant:
            data['user']['tenant_id'] = str(self.user.tenant.id)
            data['user']['tenant_subdomain'] = self.user.tenant.subdomain
            
        return data


class RegistrationInitSerializer(serializers.Serializer):
    """
    Step 1 Payload: Email, Pass, Tenant info, Profile Data.
    """
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    role = serializers.ChoiceField(choices=UserRole.choices)
    
    # Tenant Resolution
    tenant_id = serializers.UUIDField(required=False)
    
    # Profile Data (Collected upfront)
    nickname = serializers.CharField(required=False, allow_blank=True)
    bio = serializers.CharField(required=False, allow_blank=True)
    profile_image = serializers.ImageField(required=False)

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("User with this email already exists.")
        return value

    def validate(self, attrs):
        # Resolve Tenant logic (Priority: ID > Subdomain)
        request = self.context.get('request')
        tenant_id = attrs.get('tenant_id')
        
        target_tenant = None
        
        if tenant_id:
            try:
                target_tenant = Tenant.objects.get(id=tenant_id)
            except Tenant.DoesNotExist:
                raise serializers.ValidationError({"tenant_id": "Invalid Tenant ID."})
        elif hasattr(request, 'tenant') and request.tenant:
            target_tenant = request.tenant
        
        if not target_tenant and attrs.get('role') != UserRole.PLATFORM_ADMIN:
            raise serializers.ValidationError("Tenant context required.")
            
        attrs['tenant'] = target_tenant
        return attrs


class VerifyOTPSerializer(serializers.Serializer):
    """
    Step 2 Payload: Email + Code
    """
    email = serializers.EmailField()
    code = serializers.CharField(max_length=6, min_length=6)
