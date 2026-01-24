"""
User Serializers
"""
from rest_framework import serializers
from apps.users.models import User, UserProfile, UserRole
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

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
    
    # Profile fields (flattend for API convenience)
    nickname = serializers.CharField(required=False, allow_blank=True)
    bio = serializers.CharField(required=False, allow_blank=True)
    # Note: File uploads usually require Multipart/Form-data, handled separately in views often,
    # but Serializers can handle them if passed correctly.

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
        