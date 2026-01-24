"""
User Services

Encapsulates all business logic for user management.
Strictly separates logic from Views and Serializers.
"""
from django.db import transaction
from django.core.exceptions import ValidationError
from apps.users.models import User, UserProfile, UserRole

class UserService:
    """
    Service for handling User and Profile operations.
    """

    @staticmethod
    @transaction.atomic
    def create_user_with_profile(email, password, role, tenant=None, **profile_data):
        """
        Atomically creates a User and their UserProfile.
        
        Args:
            email (str): User email
            password (str): Raw password
            role (str): UserRole constant
            tenant (Tenant, optional): Tenant instance. Required if role != PLATFORM_ADMIN.
            **profile_data: Arbitrary fields for UserProfile (nickname, bio, etc.)
            
        Returns:
            User: The created user instance with profile attached.
        """
        # 1. Validation Logic
        if role != UserRole.PLATFORM_ADMIN and not tenant:
            raise ValidationError("Tenant is required for non-admin users.")
            
        if User.objects.filter(email=email).exists():
            raise ValidationError("A user with this email already exists.")

        # 2. Create User
        user = User.objects.create_user(
            email=email,
            password=password,
            role=role,
            tenant=tenant
        )

        # 3. Create Profile
        # Extract known fields to prevent error on unexpected kwargs
        nickname = profile_data.get('nickname', '')
        bio = profile_data.get('bio', '')
        profile_image = profile_data.get('profile_image', None)

        UserProfile.objects.create(
            user=user,
            nickname=nickname,
            bio=bio,
            profile_image=profile_image
        )

        return user

    @staticmethod
    def get_user_profile(user_id):
        """
        Efficiently fetches user with profile using select_related.
        """
        return User.objects.select_related('profile').get(id=user_id)
    