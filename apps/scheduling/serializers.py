from rest_framework import serializers
from django.db import transaction
from .models import Session, Booking, PricingOption, ClientPass, StaffClientAssignment
from apps.users.models import User

class StaffAssignClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffClientAssignment
        fields = ['id', 'staff', 'client']
        read_only_fields = ['id']

    def validate(self, data):
        # Ensure distinct tenant context is handled by view/mixin usually, 
        # but validation here helps.
        if data['staff'].tenant != data['client'].tenant:
            raise serializers.ValidationError("Staff and Client must belong to the same gym.")
        return data

class SessionSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.profile.nickname', read_only=True)
    
    class Meta:
        model = Session
        fields = [
            'id', 'title', 'staff', 'staff_name', 'start_time', 'end_time', 
            'capacity', 'session_type', 'meeting_url', 'is_full'
        ]

    def validate(self, data):
        if data['start_time'] >= data['end_time']:
            raise serializers.ValidationError("End time must be after start time.")
        return data

class BookingCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ['id', 'session', 'join_mode', 'music_preference']

    def validate(self, data):
        user = self.context['request'].user
        session = data['session']

        # 1. Check Capacity
        if session.is_full:
            raise serializers.ValidationError("Session is fully booked.")

        # 2. Check Staff Assignment (Constraint: Client can only book assigned staff)
        if user.role == 'client' and session.staff:
            is_assigned = user.assigned_staff_relations.filter(staff=session.staff).exists()
            if not is_assigned:
                raise serializers.ValidationError("You can only book sessions with your assigned trainer.")

        # 3. Check for existing booking
        if Booking.objects.filter(session=session, client=user, status='booked').exists():
            raise serializers.ValidationError("You are already booked for this session.")

        return data
    
    def create(self, validated_data):
        # Auto-assign client from request
        validated_data['client'] = self.context['request'].user
        return super().create(validated_data)

class BookingReadSerializer(serializers.ModelSerializer):
    session = SessionSerializer(read_only=True)
    
    class Meta:
        model = Booking
        fields = ['id', 'session', 'status', 'join_mode', 'music_preference', 'created_at']

class BookingEditSerializer(serializers.ModelSerializer):
    """
    Clients can edit details but NOT change the session.
    """
    class Meta:
        model = Booking
        fields = ['join_mode', 'music_preference']


class PricingOptionSerializer(serializers.ModelSerializer):
    """
    Serializer for Plans/Credits created by Gym Admin.
    """
    class Meta:
        model = PricingOption
        fields = [
            'id', 
            'name', 
            'price', 
            'session_credits', 
            'duration_days', 
            'fixed_start_date', 
            'fixed_expiry_date',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def validate(self, data):
        """
        Ensure strict logic between Duration vs Fixed Dates.
        """
        # Note: Model.clean() handles this, but DRF validation is better for UI feedback
        duration = data.get('duration_days')
        fixed_expiry = data.get('fixed_expiry_date')
        
        if not duration and not fixed_expiry:
            raise serializers.ValidationError("You must specify either a Duration (days) or a Fixed Expiry Date.")
        
        return data

class ClientPassSerializer(serializers.ModelSerializer):
    """
    Serializer for assigning passes to clients and viewing them.
    Includes nested details for Frontend display.
    """
    # Read-Only Fields for UI Display
    pricing_option_name = serializers.CharField(source='pricing_option.name', read_only=True)
    client_name = serializers.CharField(source='client.profile.nickname', read_only=True)
    client_email = serializers.CharField(source='client.email', read_only=True)

    class Meta:
        model = ClientPass
        fields = [
            'id', 
            'client',          # Write: UUID, Read: UUID
            'client_name',     # Read-Only: String
            'client_email',    # Read-Only: String
            'pricing_option',       # Write: UUID
            'pricing_option_name',  # Read-Only: String
            'credits_remaining',
            'start_date',
            'expiry_date',
            'is_active',
            'created_at'
        ]
        # We allow admins to edit credits/dates manually if needed, 
        # but they are auto-calculated on creation if left blank.
        extra_kwargs = {
            'credits_remaining': {'required': False},
            'start_date': {'required': False},
            'expiry_date': {'required': False},
        }

    def validate(self, data):
        """
        Ensure Client and PricingOption belong to the same Tenant.
        (Although TenantMixin usually handles filtering, this is a safety check).
        """
        # If your ViewSet filters querysets by tenant, this is implicitly safe.
        # But if you want explicit validation:
        if 'client' in data and 'pricing_option' in data:
            if data['client'].tenant_id != data['pricing_option'].tenant_id:
               raise serializers.ValidationError("Client and Pricing Option must belong to the same gym.")
        return data
    