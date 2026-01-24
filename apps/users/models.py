"""
User Models
"""
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
    
    nickname = models.CharField(max_length=50, blank=True)
    bio = models.TextField(blank=True, max_length=500)
    
    # Requires Pillow installed (which you have in requirements.txt)
    profile_image = models.ImageField(
        upload_to='profile_images/%Y/%m/',
        null=True,
        blank=True
    )
    
    # We can add custom fields here
    # e.g., trainer_specialties = models.JSONField(default=list)

    def __str__(self):
        return f"Profile for {self.user.email}"
    