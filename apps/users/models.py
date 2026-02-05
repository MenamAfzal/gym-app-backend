"""
User Models
"""
from datetime import timezone
import hashlib
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.translation import gettext_lazy as _
from apps.core.tenants.models import Tenant
from core_models.mixins.uuid_mixin import UUIDMixin
from core_models.mixins.timestamps import TimestampMixin

class UserRole(models.TextChoices):
    """
    Role-based access control constants.
    """
    PLATFORM_ADMIN = 'platform_admin', _('Platform Admin')
    GYM_OWNER = 'gym_owner', _('Gym Owner')
    GYM_MANAGER = 'gym_manager', _('Gym Manager')
    TRAINER = 'trainer', _('Trainer')
    CLIENT = 'client', _('Client')

class UserManager(BaseUserManager):
    """Custom manager for email-based authentication."""
    
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        # Force lower case for consistency
        email = email.lower()
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', UserRole.PLATFORM_ADMIN)
        return self.create_user(email, password, **extra_fields)

class User(UUIDMixin, AbstractUser):
    """
    Custom User model supporting multi-tenancy and roles.
    Optimized for high-frequency Auth queries (minimal fields).
    """
    username = None  # Disabled
    email = models.EmailField(_('email address'), unique=True)
    
    # Tenant Link (Nullable only for Platform Admins)
    tenant = models.ForeignKey(
        Tenant, 
        on_delete=models.CASCADE, 
        related_name='users',
        null=True, 
        blank=True,
        db_index=True # Optimize lookups
    )

    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.CLIENT,
        db_index=True # Optimize permission checks
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        ordering = ['-date_joined']
        indexes = [
            models.Index(fields=['email', 'tenant']),
        ]

    def __str__(self):
        return f"{self.email} ({self.role})"


class GenderChoices(models.TextChoices):
    """Gender options for user profiles."""
    MALE = 'male', _('Male')
    FEMALE = 'female', _('Female')
    OTHER = 'other', _('Other')
    PREFER_NOT_TO_SAY = 'prefer_not_to_say', _('Prefer not to say')


class UserProfile(UUIDMixin, TimestampMixin):
    """
    Extended user profile data.
    Separated from User model to keep auth sessions lightweight.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile'
    )
    
    # Basic Info
    nickname = models.CharField(max_length=50, blank=True)
    bio = models.TextField(blank=True, max_length=500)
    
    # Profile Image
    profile_image = models.ImageField(
        upload_to='profile_images/%Y/%m/',
        null=True,
        blank=True
    )
    
    # Personal Information
    first_name = models.CharField(max_length=100, blank=True, help_text="User's first name")
    last_name = models.CharField(max_length=100, blank=True, help_text="User's last name")
    phone_number = models.CharField(max_length=20, blank=True, help_text="Contact phone number")
    date_of_birth = models.DateField(null=True, blank=True, help_text="Date of birth")
    gender = models.CharField(
        max_length=20,
        choices=GenderChoices.choices,
        blank=True,
        help_text="Gender"
    )
    
    # Address Information
    address = models.CharField(max_length=255, blank=True, help_text="Street address")
    city = models.CharField(max_length=100, blank=True, help_text="City")
    country = models.CharField(max_length=100, blank=True, help_text="Country")
    postal_code = models.CharField(max_length=20, blank=True, help_text="Postal/ZIP code")
    
    # Emergency Contact
    emergency_contact_name = models.CharField(
        max_length=100, 
        blank=True, 
        help_text="Emergency contact full name"
    )
    emergency_contact_phone = models.CharField(
        max_length=20, 
        blank=True, 
        help_text="Emergency contact phone number"
    )

    def __str__(self):
        return f"Profile for {self.user.email}"


class OTPPurpose(models.TextChoices):
    REGISTRATION = "registration", "Registration"
    PASSWORD_RESET = "password_reset", "Password reset"

class EmailOTP(models.Model):
    """
    Short-lived One Time Password for verification.
    """
    email = models.EmailField(db_index=True)
    otp_hash = models.CharField(max_length=128)
    purpose = models.CharField(max_length=32, choices=OTPPurpose.choices, default=OTPPurpose.REGISTRATION)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True, default="")

    class Meta:
        indexes = [
            models.Index(fields=["email", "purpose"]),
            models.Index(fields=["expires_at"]),
        ]

    def is_expired(self):
        return timezone.now() >= self.expires_at

    @staticmethod
    def hash_code(code: str, salt: str = "") -> str:
        return hashlib.sha256(f"{salt}{code}".encode("utf-8")).hexdigest()

    def check_code(self, code: str, salt: str = "") -> bool:
        return self.otp_hash == self.hash_code(code, salt)


class PendingRegistration(models.Model):
    """
    Staging area for users who haven't verified OTP yet.
    Stores the Tenant reference to ensure the final user is created in the right context.
    """
    email = models.EmailField(unique=True)
    password_hash = models.CharField(max_length=128)
    role = models.CharField(max_length=20)
    
    # Tenant Context (Critical for your SaaS)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='pending_registrations')

    # Basic Profile Fields
    nickname = models.CharField(max_length=50, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    
    # Temporary Image Storage
    profile_image = models.ImageField(upload_to='pending_uploads/', null=True, blank=True)
    
    # Personal Information (mirrors UserProfile)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=20, choices=GenderChoices.choices, blank=True)
    
    # Address Information
    address = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    
    # Emergency Contact
    emergency_contact_name = models.CharField(max_length=100, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Pending: {self.email} for {self.tenant.subdomain}"
    