from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, UserProfile

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'Profile'

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # The forms to add and change user instances
    # Using default forms from BaseUserAdmin usually works if USERNAME_FIELD is set correctly
    
    ordering = ['email']
    list_display = ['email', 'first_name', 'last_name', 'role', 'tenant', 'is_staff', 'is_active']
    list_filter = ['role', 'tenant', 'is_staff', 'is_active', 'groups']
    search_fields = ['email', 'first_name', 'last_name']

    # Fieldsets for the change view
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'role', 'tenant')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )

    # Fieldsets for the add view
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password', 'first_name', 'last_name', 'role', 'tenant'),
        }),
    )

    inlines = [UserProfileInline]

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'nickname', 'created_at', 'updated_at']
    search_fields = ['user__email', 'nickname', 'bio']
    list_filter = ['created_at']
