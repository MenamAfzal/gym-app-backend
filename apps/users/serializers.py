"""
User Serializers
"""
from rest_framework import serializers
from apps.users.models import User, UserProfile, UserRole, OTPPurpose, GenderChoices
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from apps.core.tenants.models import Tenant
from apps.users.services import UserService

class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for user profile data with all extended fields."""
    
    class Meta:
        model = UserProfile
        fields = [
            'nickname', 'bio', 'profile_image',
            # Personal Information
            'first_name', 'last_name', 'phone_number', 'date_of_birth', 'gender',
            'height', 'weight',
            # Address Information
            'address', 'city', 'country', 'postal_code',
            # Emergency Contact
            'emergency_contact_name', 'emergency_contact_phone',
        ]
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
    All profile fields are optional for backward compatibility.
    """
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    role = serializers.ChoiceField(choices=UserRole.choices)
    tenant_id = serializers.UUIDField(required=False)
    
    # Basic Profile Fields
    nickname = serializers.CharField(required=False, allow_blank=True)
    bio = serializers.CharField(required=False, allow_blank=True)
    profile_image = serializers.ImageField(required=False, allow_null=True)
    
    # Personal Information (all optional)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    phone_number = serializers.CharField(required=False, allow_blank=True, max_length=20)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    gender = serializers.ChoiceField(choices=GenderChoices.choices, required=False, allow_blank=True)
    height = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    weight = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    
    # Address Information (all optional)
    address = serializers.CharField(required=False, allow_blank=True, max_length=255)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    country = serializers.CharField(required=False, allow_blank=True, max_length=100)
    postal_code = serializers.CharField(required=False, allow_blank=True, max_length=20)
    
    # Emergency Contact (all optional)
    emergency_contact_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    emergency_contact_phone = serializers.CharField(required=False, allow_blank=True, max_length=20)

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
    All profile fields are optional for flexible registration flows.
    """
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)
    role = serializers.ChoiceField(choices=UserRole.choices)
    
    # Tenant Resolution
    tenant_id = serializers.UUIDField(required=False)
    
    # Basic Profile Data (Collected upfront)
    nickname = serializers.CharField(required=False, allow_blank=True)
    bio = serializers.CharField(required=False, allow_blank=True)
    profile_image = serializers.ImageField(required=False)
    
    # Personal Information (all optional)
    first_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    last_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    phone_number = serializers.CharField(required=False, allow_blank=True, max_length=20)
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    gender = serializers.ChoiceField(choices=GenderChoices.choices, required=False, allow_blank=True)
    height = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    weight = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    
    # Address Information (all optional)
    address = serializers.CharField(required=False, allow_blank=True, max_length=255)
    city = serializers.CharField(required=False, allow_blank=True, max_length=100)
    country = serializers.CharField(required=False, allow_blank=True, max_length=100)
    postal_code = serializers.CharField(required=False, allow_blank=True, max_length=20)
    
    # Emergency Contact (all optional)
    emergency_contact_name = serializers.CharField(required=False, allow_blank=True, max_length=100)
    emergency_contact_phone = serializers.CharField(required=False, allow_blank=True, max_length=20)

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


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True, min_length=8)
    confirm_password = serializers.CharField(required=True)

    def validate(self, attrs):
        if attrs['new_password'] != attrs['confirm_password']:
            raise serializers.ValidationError({"confirm_password": "Passwords do not match."})
        return attrs

    def validate_old_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Old password is incorrect.")
        return value
    

class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer()

    class Meta:
        model = User
        fields = ['id', 'email', 'role', 'profile', 'date_joined']
        read_only_fields = ['id', 'email', 'role', 'date_joined']

    def update(self, instance, validated_data):
        # Extract nested profile data
        profile_data = validated_data.pop('profile', {})
        
        # Delegate to Service Layer
        return UserService.update_user_profile(
            user=instance, 
            user_data=validated_data, 
            profile_data=profile_data
        )

class ForgotPasswordInitSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if not User.objects.filter(email=value).exists():
            raise serializers.ValidationError("No user found with this email address.")
        return value

class ForgotPasswordVerifySerializer(serializers.Serializer):
    email = serializers.EmailField()
    code = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(required=True, min_length=8)


class ClientDetailedSchedulingSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)
    stats = serializers.SerializerMethodField()
    next_class_session = serializers.SerializerMethodField()
    previous_class_session = serializers.SerializerMethodField()
    next_appointment = serializers.SerializerMethodField()
    previous_appointment = serializers.SerializerMethodField()
    packages = serializers.SerializerMethodField()
    bookings = serializers.SerializerMethodField()
    appointments = serializers.SerializerMethodField()
    facility_access_logs = serializers.SerializerMethodField()
    waitlists = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'role', 'profile', 'date_joined',
            'stats', 'next_class_session', 'previous_class_session',
            'next_appointment', 'previous_appointment', 'packages',
            'bookings', 'appointments', 'facility_access_logs', 'waitlists'
        ]
        read_only_fields = fields

    def get_stats(self, obj):
        from django.utils import timezone
        now = timezone.now()
        
        all_bookings = getattr(obj, 'prefetched_bookings', list(obj.bookings.all()))
        all_packages = getattr(obj, 'prefetched_packages', list(obj.packages.all()))
        all_appointments = getattr(obj, 'prefetched_appointments', list(obj.appointments.all()))
        all_logs = getattr(obj, 'prefetched_logs', list(obj.facility_access_logs.all()))
        
        total_classes_booked = sum(1 for b in all_bookings if b.status == 'booked')
        total_classes_attended = sum(1 for b in all_bookings if b.status in ['attended', 'checked_in'])
        total_classes_no_show = sum(1 for b in all_bookings if b.status == 'no_show')
        total_classes_cancelled = sum(1 for b in all_bookings if b.status == 'cancelled')
        
        total_packages_purchased = len(all_packages)
        total_active_packages = sum(1 for p in all_packages if p.credits_remaining > 0 and p.expires_at > now)
        total_active_credits_remaining = sum(p.credits_remaining for p in all_packages if p.credits_remaining > 0 and p.expires_at > now)
        
        total_appointments_booked = len(all_appointments)
        total_facility_visits = len(all_logs)
        
        return {
            "total_classes_booked": total_classes_booked,
            "total_classes_attended": total_classes_attended,
            "total_classes_no_show": total_classes_no_show,
            "total_classes_cancelled": total_classes_cancelled,
            "total_packages_purchased": total_packages_purchased,
            "total_active_packages": total_active_packages,
            "total_active_credits_remaining": total_active_credits_remaining,
            "total_appointments_booked": total_appointments_booked,
            "total_facility_visits": total_facility_visits
        }

    def get_next_class_session(self, obj):
        from django.utils import timezone
        now = timezone.now()
        
        all_bookings = getattr(obj, 'prefetched_bookings', list(obj.bookings.all()))
        upcoming = [b for b in all_bookings if b.status == 'booked' and b.session.start_at > now]
        if not upcoming:
            return None
        next_booking = min(upcoming, key=lambda b: b.session.start_at)
        s = next_booking.session
        return {
            "booking_id": str(next_booking.id),
            "session_id": str(s.id),
            "class_name": s.template.name,
            "start_at": s.start_at.isoformat(),
            "end_at": s.end_at.isoformat(),
            "location_name": s.template.location.name,
            "room_name": s.room.name if s.room else "",
            "staff_name": s.staff.profile.nickname if s.staff and hasattr(s.staff, 'profile') else (s.staff.email if s.staff else ""),
            "status": next_booking.status
        }

    def get_previous_class_session(self, obj):
        from django.utils import timezone
        now = timezone.now()
        
        all_bookings = getattr(obj, 'prefetched_bookings', list(obj.bookings.all()))
        past = [b for b in all_bookings if b.session.start_at <= now]
        if not past:
            return None
        prev_booking = max(past, key=lambda b: b.session.start_at)
        s = prev_booking.session
        return {
            "booking_id": str(prev_booking.id),
            "session_id": str(s.id),
            "class_name": s.template.name,
            "start_at": s.start_at.isoformat(),
            "end_at": s.end_at.isoformat(),
            "location_name": s.template.location.name,
            "room_name": s.room.name if s.room else "",
            "staff_name": s.staff.profile.nickname if s.staff and hasattr(s.staff, 'profile') else (s.staff.email if s.staff else ""),
            "status": prev_booking.status
        }

    def get_next_appointment(self, obj):
        from django.utils import timezone
        now = timezone.now()
        
        all_appointments = getattr(obj, 'prefetched_appointments', list(obj.appointments.all()))
        upcoming = [a for a in all_appointments if a.status == 'scheduled' and a.start_at > now]
        if not upcoming:
            return None
        next_appt = min(upcoming, key=lambda a: a.start_at)
        return {
            "appointment_id": str(next_appt.id),
            "provider_name": next_appt.provider.profile.nickname if next_appt.provider and hasattr(next_appt.provider, 'profile') else (next_appt.provider.email if next_appt.provider else ""),
            "start_at": next_appt.start_at.isoformat(),
            "end_at": next_appt.end_at.isoformat(),
            "location_name": next_appt.location.name,
            "room_name": next_appt.room.name if next_appt.room else "",
            "status": next_appt.status
        }

    def get_previous_appointment(self, obj):
        from django.utils import timezone
        now = timezone.now()
        
        all_appointments = getattr(obj, 'prefetched_appointments', list(obj.appointments.all()))
        past = [a for a in all_appointments if a.start_at <= now or a.status == 'completed']
        if not past:
            return None
        prev_appt = max(past, key=lambda a: a.start_at)
        return {
            "appointment_id": str(prev_appt.id),
            "provider_name": prev_appt.provider.profile.nickname if prev_appt.provider and hasattr(prev_appt.provider, 'profile') else (prev_appt.provider.email if prev_appt.provider else ""),
            "start_at": prev_appt.start_at.isoformat(),
            "end_at": prev_appt.end_at.isoformat(),
            "location_name": prev_appt.location.name,
            "room_name": prev_appt.room.name if prev_appt.room else "",
            "status": prev_appt.status
        }

    def get_packages(self, obj):
        all_packages = getattr(obj, 'prefetched_packages', list(obj.packages.all()))
        return [{
            "id": str(p.id),
            "package_type_name": p.package_type.name,
            "credits_remaining": p.credits_remaining,
            "purchased_at": p.purchased_at.isoformat(),
            "expires_at": p.expires_at.isoformat()
        } for p in all_packages]

    def get_bookings(self, obj):
        all_bookings = getattr(obj, 'prefetched_bookings', list(obj.bookings.all()))
        all_bookings.sort(key=lambda b: b.session.start_at, reverse=True)
        return [{
            "id": str(b.id),
            "session_id": str(b.session.id),
            "class_name": b.session.template.name,
            "start_at": b.session.start_at.isoformat(),
            "end_at": b.session.end_at.isoformat(),
            "location_name": b.session.template.location.name,
            "room_name": b.session.room.name if b.session.room else "",
            "staff_name": b.session.staff.profile.nickname if b.session.staff and hasattr(b.session.staff, 'profile') else (b.session.staff.email if b.session.staff else ""),
            "status": b.status,
            "join_mode": b.join_mode,
            "music_preference": b.music_preference,
            "checked_in_at": b.checked_in_at.isoformat() if b.checked_in_at else None
        } for b in all_bookings]

    def get_appointments(self, obj):
        all_appointments = getattr(obj, 'prefetched_appointments', list(obj.appointments.all()))
        all_appointments.sort(key=lambda a: a.start_at, reverse=True)
        return [{
            "id": str(a.id),
            "provider_name": a.provider.profile.nickname if a.provider and hasattr(a.provider, 'profile') else (a.provider.email if a.provider else ""),
            "start_at": a.start_at.isoformat(),
            "end_at": a.end_at.isoformat(),
            "location_name": a.location.name,
            "room_name": a.room.name if a.room else "",
            "status": a.status
        } for a in all_appointments]

    def get_facility_access_logs(self, obj):
        all_logs = getattr(obj, 'prefetched_logs', list(obj.facility_access_logs.all()))
        all_logs.sort(key=lambda log: log.checked_in_at, reverse=True)
        return [{
            "id": str(log.id),
            "location_name": log.location.name,
            "checked_in_at": log.checked_in_at.isoformat(),
            "checked_out_at": log.checked_out_at.isoformat() if log.checked_out_at else None
        } for log in all_logs]

    def get_waitlists(self, obj):
        all_waitlists = getattr(obj, 'prefetched_waitlists', list(obj.waitlists.all()))
        all_waitlists.sort(key=lambda w: w.created_at, reverse=True)
        return [{
            "id": str(w.id),
            "session_id": str(w.session.id),
            "class_name": w.session.template.name,
            "start_at": w.session.start_at.isoformat(),
            "position": w.position,
            "status": w.status,
            "offered_at": w.offered_at.isoformat() if w.offered_at else None,
            "expires_at": w.expires_at.isoformat() if w.expires_at else None
        } for w in all_waitlists]


class StaffDetailedSchedulingSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)
    stats = serializers.SerializerMethodField()
    next_class_session = serializers.SerializerMethodField()
    previous_class_session = serializers.SerializerMethodField()
    next_appointment = serializers.SerializerMethodField()
    previous_appointment = serializers.SerializerMethodField()
    locations = serializers.SerializerMethodField()
    availabilities = serializers.SerializerMethodField()
    assigned_clients = serializers.SerializerMethodField()
    recent_classes_led = serializers.SerializerMethodField()
    recent_appointments = serializers.SerializerMethodField()
    substitute_requests_raised = serializers.SerializerMethodField()
    substitute_requests_accepted = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'role', 'profile', 'date_joined',
            'stats', 'next_class_session', 'previous_class_session',
            'next_appointment', 'previous_appointment', 'locations',
            'availabilities', 'assigned_clients', 'recent_classes_led',
            'recent_appointments', 'substitute_requests_raised', 'substitute_requests_accepted'
        ]
        read_only_fields = fields

    def get_stats(self, obj):
        from django.utils import timezone
        now = timezone.now()
        
        all_sessions = getattr(obj, 'prefetched_sessions', list(obj.sessions.all()))
        all_appointments = getattr(obj, 'prefetched_provider_appointments', list(obj.provider_appointments.all()))
        all_clients = getattr(obj, 'prefetched_assigned_clients', list(obj.assigned_client_relations.all()))
        all_raised_subs = getattr(obj, 'prefetched_raised_subs', list(obj.requested_substitutes.all()))
        all_accepted_subs = getattr(obj, 'prefetched_accepted_subs', list(obj.accepted_substitutes.all()))
        
        total_upcoming_classes = sum(1 for s in all_sessions if s.status == 'scheduled' and s.start_at > now)
        total_past_classes = sum(1 for s in all_sessions if s.start_at <= now)
        
        total_minutes = sum(s.template.duration_min for s in all_sessions if s.start_at <= now and s.status in ['scheduled', 'completed'])
        total_hours_taught = float(total_minutes) / 60.0
        
        total_private_appointments = len(all_appointments)
        total_assigned_clients = len(all_clients)
        total_substitute_requests_raised = len(all_raised_subs)
        total_substitute_requests_accepted = len(all_accepted_subs)
        
        return {
            "total_upcoming_classes": total_upcoming_classes,
            "total_past_classes": total_past_classes,
            "total_hours_taught": round(total_hours_taught, 2),
            "total_private_appointments": total_private_appointments,
            "total_assigned_clients": total_assigned_clients,
            "total_substitute_requests_raised": total_substitute_requests_raised,
            "total_substitute_requests_accepted": total_substitute_requests_accepted
        }

    def get_next_class_session(self, obj):
        from django.utils import timezone
        now = timezone.now()
        
        all_sessions = getattr(obj, 'prefetched_sessions', list(obj.sessions.all()))
        upcoming = [s for s in all_sessions if s.status == 'scheduled' and s.start_at > now]
        if not upcoming:
            return None
        next_session = min(upcoming, key=lambda s: s.start_at)
        return {
            "session_id": str(next_session.id),
            "class_name": next_session.template.name,
            "start_at": next_session.start_at.isoformat(),
            "end_at": next_session.end_at.isoformat(),
            "location_name": next_session.template.location.name,
            "room_name": next_session.room.name if next_session.room else "",
            "status": next_session.status
        }

    def get_previous_class_session(self, obj):
        from django.utils import timezone
        now = timezone.now()
        
        all_sessions = getattr(obj, 'prefetched_sessions', list(obj.sessions.all()))
        past = [s for s in all_sessions if s.start_at <= now]
        if not past:
            return None
        prev_session = max(past, key=lambda s: s.start_at)
        return {
            "session_id": str(prev_session.id),
            "class_name": prev_session.template.name,
            "start_at": prev_session.start_at.isoformat(),
            "end_at": prev_session.end_at.isoformat(),
            "location_name": prev_session.template.location.name,
            "room_name": prev_session.room.name if prev_session.room else "",
            "status": prev_session.status
        }

    def get_next_appointment(self, obj):
        from django.utils import timezone
        now = timezone.now()
        
        all_appointments = getattr(obj, 'prefetched_provider_appointments', list(obj.provider_appointments.all()))
        upcoming = [a for a in all_appointments if a.status == 'scheduled' and a.start_at > now]
        if not upcoming:
            return None
        next_appt = min(upcoming, key=lambda a: a.start_at)
        return {
            "appointment_id": str(next_appt.id),
            "client_email": next_appt.client.email,
            "client_name": next_appt.client.profile.nickname if hasattr(next_appt.client, 'profile') and next_appt.client.profile.nickname else next_appt.client.email,
            "start_at": next_appt.start_at.isoformat(),
            "end_at": next_appt.end_at.isoformat(),
            "location_name": next_appt.location.name,
            "room_name": next_appt.room.name if next_appt.room else "",
            "status": next_appt.status
        }

    def get_previous_appointment(self, obj):
        from django.utils import timezone
        now = timezone.now()
        
        all_appointments = getattr(obj, 'prefetched_provider_appointments', list(obj.provider_appointments.all()))
        past = [a for a in all_appointments if a.start_at <= now or a.status == 'completed']
        if not past:
            return None
        prev_appt = max(past, key=lambda a: a.start_at)
        return {
            "appointment_id": str(prev_appt.id),
            "client_email": prev_appt.client.email,
            "client_name": prev_appt.client.profile.nickname if hasattr(prev_appt.client, 'profile') and prev_appt.client.profile.nickname else prev_appt.client.email,
            "start_at": prev_appt.start_at.isoformat(),
            "end_at": prev_appt.end_at.isoformat(),
            "location_name": prev_appt.location.name,
            "room_name": prev_appt.room.name if prev_appt.room else "",
            "status": prev_appt.status
        }

    def get_locations(self, obj):
        all_staff_locations = getattr(obj, 'prefetched_staff_locations', list(obj.staff_locations.all()))
        return [{
            "id": str(sl.location.id),
            "name": sl.location.name,
            "address": sl.location.address
        } for sl in all_staff_locations]

    def get_availabilities(self, obj):
        all_availabilities = getattr(obj, 'prefetched_availabilities', list(obj.availabilities.all()))
        return [{
            "id": str(a.id),
            "weekday_or_date": a.weekday_or_date,
            "start_time": a.start_time.isoformat() if hasattr(a.start_time, 'isoformat') else str(a.start_time),
            "end_time": a.end_time.isoformat() if hasattr(a.end_time, 'isoformat') else str(a.end_time),
            "is_blackout": a.is_blackout
        } for a in all_availabilities]

    def get_assigned_clients(self, obj):
        all_clients = getattr(obj, 'prefetched_assigned_clients', list(obj.assigned_client_relations.all()))
        return [{
            "assignment_id": str(ac.id),
            "client_id": str(ac.client.id),
            "client_email": ac.client.email,
            "client_name": ac.client.profile.nickname if hasattr(ac.client, 'profile') and ac.client.profile.nickname else ac.client.email
        } for ac in all_clients]

    def get_recent_classes_led(self, obj):
        all_sessions = getattr(obj, 'prefetched_sessions', list(obj.sessions.all()))
        all_sessions.sort(key=lambda s: s.start_at, reverse=True)
        return [{
            "session_id": str(s.id),
            "class_name": s.template.name,
            "start_at": s.start_at.isoformat(),
            "end_at": s.end_at.isoformat(),
            "location_name": s.template.location.name,
            "room_name": s.room.name if s.room else "",
            "status": s.status,
            "capacity": s.capacity
        } for s in all_sessions[:20]]

    def get_recent_appointments(self, obj):
        all_appointments = getattr(obj, 'prefetched_provider_appointments', list(obj.provider_appointments.all()))
        all_appointments.sort(key=lambda a: a.start_at, reverse=True)
        return [{
            "id": str(a.id),
            "client_email": a.client.email,
            "client_name": a.client.profile.nickname if hasattr(a.client, 'profile') and a.client.profile.nickname else a.client.email,
            "start_at": a.start_at.isoformat(),
            "end_at": a.end_at.isoformat(),
            "location_name": a.location.name,
            "room_name": a.room.name if a.room else "",
            "status": a.status
        } for a in all_appointments[:20]]

    def get_substitute_requests_raised(self, obj):
        all_raised_subs = getattr(obj, 'prefetched_raised_subs', list(obj.requested_substitutes.all()))
        all_raised_subs.sort(key=lambda sr: sr.created_at, reverse=True)
        return [{
            "id": str(sr.id),
            "session_id": str(sr.session.id),
            "class_name": sr.session.template.name,
            "start_at": sr.session.start_at.isoformat(),
            "status": sr.status,
            "accepted_by_staff_email": sr.accepted_by_staff.email if sr.accepted_by_staff else None
        } for sr in all_raised_subs]

    def get_substitute_requests_accepted(self, obj):
        all_accepted_subs = getattr(obj, 'prefetched_accepted_subs', list(obj.accepted_substitutes.all()))
        all_accepted_subs.sort(key=lambda sr: sr.created_at, reverse=True)
        return [{
            "id": str(sr.id),
            "session_id": str(sr.session.id),
            "class_name": sr.session.template.name,
            "start_at": sr.session.start_at.isoformat(),
            "status": sr.status,
            "requested_by_staff_email": sr.requested_by_staff.email
        } for sr in all_accepted_subs]