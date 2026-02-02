"""
User Services

Encapsulates all business logic for user management.
Strictly separates logic from Views and Serializers.
"""
from django.db import transaction
from django.core.exceptions import ValidationError
from apps.users.models import User, UserProfile, UserRole
import secrets
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.core.mail import send_mail
from django.utils.html import strip_tags
from django.core.mail import EmailMultiAlternatives  
from django.template.loader import render_to_string
from rest_framework import serializers

from apps.users.models import (
    PendingRegistration, EmailOTP, OTPPurpose, UserProfile, UserRole
)
# Assuming you have a Tenant model imported
from apps.core.tenants.models import Tenant

User = get_user_model()

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
    
    @staticmethod
    @transaction.atomic
    def update_user_profile(user, user_data=None, profile_data=None):
        """
        Optimized atomic update for User and UserProfile.
        """
        # 1. Update User fields if provided (e.g., email or name)
        if user_data:
            for attr, value in user_data.items():
                setattr(user, attr, value)
            user.save()

        # 2. Update Profile fields
        if profile_data:
            profile, _ = UserProfile.objects.get_or_create(user=user)
            
            # Special handling for images to prevent clearing if not provided in partial update
            profile_image = profile_data.pop('profile_image', None)
            if profile_image:
                profile.profile_image = profile_image
            
            for attr, value in profile_data.items():
                setattr(profile, attr, value)
            profile.save()
        
        return user

    @staticmethod
    @transaction.atomic
    def delete_user(user):
        """
        Handles user deletion with safety checks.
        In SaaS, often better to 'deactivate' rather than hard-delete.
        """
        # Safety: Ensure we don't accidentally delete the last Gym Owner
        if user.role == UserRole.GYM_OWNER:
            owner_count = User.objects.filter(tenant=user.tenant, role=UserRole.GYM_OWNER).count()
            if owner_count <= 1:
                raise ValidationError("Cannot delete the last Gym Owner. Transfer ownership first.")
        
        # Hard delete cascades to UserProfile and PendingRegistrations
        user.delete()
        return True
    

class AuthService:
    """
    Centralized Authentication Logic.
    """

    @staticmethod
    def create_pending_registration(validated_data: dict, files=None):
        """
        Step 1: Store registration data temporarily.
        """
        email = validated_data["email"]
        tenant = validated_data["tenant"] # This is the resolved Tenant instance
        
        # Security: Hash password immediately
        password_hash = make_password(validated_data["password"])

        # Update or Create Pending Record
        # If user retries registration before verifying, we update the existing pending record
        defaults = {
            "password_hash": password_hash,
            "role": validated_data["role"],
            "tenant": tenant,
            "nickname": validated_data.get("nickname", ""),
            "bio": validated_data.get("bio", ""),
        }

        # Handle Image if present
        if files and 'profile_image' in files:
            defaults['profile_image'] = files['profile_image']

        PendingRegistration.objects.update_or_create(
            email=email,
            defaults=defaults
        )

    @staticmethod
    def create_email_otp(email: str, purpose: str) -> str:
        """
        Generates a 6-digit OTP and stores the hash.
        """
        # Logic: Don't allow registration OTP if user already exists
        if purpose == OTPPurpose.REGISTRATION and User.objects.filter(email=email).exists():
            raise serializers.ValidationError("This email is already registered.")

        # Rate Limiting (Cooldown)
        now = timezone.now()
        latest = EmailOTP.objects.filter(email=email, purpose=purpose).order_by("-created_at").first()
        cooldown = 60 # 60 seconds
        
        if latest and (now - latest.created_at).total_seconds() < cooldown:
            raise serializers.ValidationError(f"Please wait {cooldown} seconds before requesting a new code.")

        # Generate Code
        code = ''.join(str(secrets.randbelow(10)) for _ in range(6))
        otp_hash = EmailOTP.hash_code(code)
        expires_at = now + timedelta(seconds=300) # 5 minutes expiry

        EmailOTP.objects.create(
            email=email, 
            otp_hash=otp_hash, 
            purpose=purpose, 
            expires_at=expires_at
        )

        return code

    @staticmethod
    def send_email_otp(email: str, code: str, purpose: str):
        """Send OTP email to user, called explicitly once."""
        is_registration = purpose == OTPPurpose.REGISTRATION
        subject = (
            "Welcome – Verify Your Email" if is_registration
            else "Password Reset Code"
        )
        template = (
            "emails/registration_otp.html" if is_registration
            else "emails/password_reset_otp.html"
        )

        html_message = render_to_string(template, {"code": code})
        msg = EmailMultiAlternatives(subject, strip_tags(html_message), settings.DEFAULT_FROM_EMAIL, [email])
        msg.attach_alternative(html_message, "text/html")
        msg.send(fail_silently=False)

    @staticmethod
    def verify_email_otp(email: str, code: str, purpose: str, request=None):
        """
        Verifies the provided code against the hash.
        """
        now = timezone.now()
        otp = EmailOTP.objects.filter(
            email=email, purpose=purpose, used=False, expires_at__gte=now
        ).order_by("-created_at").first()

        if not otp:
            raise serializers.ValidationError({"otp": "Invalid or expired OTP."})

        if not otp.check_code(code):
            otp.attempts += 1
            otp.save()
            raise serializers.ValidationError({"otp": "Invalid code."})

        # Mark used
        otp.used = True
        otp.save()
        return True

    @staticmethod
    @transaction.atomic
    def finalize_registration(email: str) -> User: # type: ignore
        """
        Step 2: Move data from PendingRegistration to User + UserProfile.
        """
        try:
            pending = PendingRegistration.objects.select_related('tenant').get(email=email)
        except PendingRegistration.DoesNotExist:
            raise serializers.ValidationError("Registration session expired or invalid.")

        # 1. Create User (Strict Tenant Isolation)
        user = User.objects.create(
            email=pending.email,
            password=pending.password_hash, # Already hashed
            role=pending.role,
            tenant=pending.tenant, # Crucial: Link to Tenant
            is_active=True,
            # We assume email is verified because they passed OTP
            # is_email_verified=True (Add this field to User model if you strictly need it)
        )

        # 2. Create/Update Profile
        # Note: User creation signal might create an empty profile, so we update it.
        profile, created = UserProfile.objects.get_or_create(user=user)
        profile.nickname = pending.nickname
        profile.bio = pending.bio
        
        # Move Image: Assign the file object from Pending to Profile
        if pending.profile_image:
            profile.profile_image.save(pending.profile_image.name, pending.profile_image, save=False)
        
        profile.save()

        # 3. Cleanup
        pending.delete()

        return user
    
    @staticmethod
    @transaction.atomic
    def change_password(user, new_password):
        """
        Updates the user's password and handles security side effects.
        """
        user.set_password(new_password)
        user.save()
        
        # Optional: Invalidate tokens if rotation/blacklisting is enabled
        # Optional: AuthService.send_security_notification(user.email, "password_change")
        
        return user
        