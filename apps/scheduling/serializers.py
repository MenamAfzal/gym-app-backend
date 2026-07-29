from datetime import timedelta, datetime
from django.utils import timezone
from rest_framework import serializers
from django.db import transaction
from django.db.models import Q
from .models import (
    Location, Room, StaffLocation, StaffAvailability, ClassTemplate,
    RecurrenceRule, ClassSession, Booking, Appointment, Waitlist,
    SubstituteRequest, PackageType, Package, Payment, CancellationPolicy,
    Notification, StaffClientAssignment, FacilityAccessLog, PayoutRun, PlatformLedger
)
from apps.users.models import User

class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ['id', 'name', 'address', 'timezone', 'phone', 'created_at']
        read_only_fields = ['id', 'created_at']


class RoomSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)

    class Meta:
        model = Room
        fields = ['id', 'location', 'location_name', 'name', 'capacity', 'equipment_tags', 'created_at']
        read_only_fields = ['id', 'location_name', 'created_at']


class StaffLocationSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.profile.nickname', read_only=True)
    staff_email = serializers.CharField(source='staff.email', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)

    class Meta:
        model = StaffLocation
        fields = ['id', 'staff', 'staff_name', 'staff_email', 'location', 'location_name', 'created_at']
        read_only_fields = ['id', 'staff_name', 'staff_email', 'location_name', 'created_at']


class StaffAvailabilitySerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.profile.nickname', read_only=True)

    class Meta:
        model = StaffAvailability
        fields = ['id', 'staff', 'staff_name', 'weekday_or_date', 'start_time', 'end_time', 'is_blackout', 'created_at']
        read_only_fields = ['id', 'staff_name', 'created_at']


class ClassTemplateSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)

    class Meta:
        model = ClassTemplate
        fields = [
            'id', 'location', 'location_name', 'name', 'description', 
            'duration_min', 'default_capacity', 'intensity', 'category', 'created_at'
        ]
        read_only_fields = ['id', 'location_name', 'created_at']


class RecurrenceRuleSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source='template.name', read_only=True)
    room_name = serializers.CharField(source='room.name', read_only=True)
    staff_name = serializers.CharField(source='staff.profile.nickname', read_only=True)

    class Meta:
        model = RecurrenceRule
        fields = [
            'id', 'template', 'template_name', 'days_of_week', 'start_date', 
            'end_date', 'start_time', 'room', 'room_name', 'staff', 'staff_name', 'created_at'
        ]
        read_only_fields = ['id', 'template_name', 'room_name', 'staff_name', 'created_at']

    def validate(self, data):
        days = data.get('days_of_week')
        if not isinstance(days, list) or not all(isinstance(d, str) for d in days):
            raise serializers.ValidationError({"days_of_week": "Must be a list of weekday strings (e.g. ['monday', 'tuesday'])"})
        
        valid_days = {'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'}
        for day in days:
            if day.lower() not in valid_days:
                raise serializers.ValidationError({"days_of_week": f"'{day}' is not a valid weekday name."})
        
        if data.get('start_date') >= data.get('end_date'):
            raise serializers.ValidationError("End date must be after start date.")
            
        return data


class ClassSessionSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source='template.name', read_only=True)
    description = serializers.CharField(source='template.description', read_only=True, default='')
    duration_min = serializers.IntegerField(source='template.duration_min', read_only=True, default=0)
    category = serializers.CharField(source='template.category', read_only=True, default='')
    intensity = serializers.CharField(source='template.intensity', read_only=True, default='')
    location_id = serializers.UUIDField(source='template.location_id', read_only=True, default=None)
    location_name = serializers.CharField(source='template.location.name', read_only=True, default='')
    
    room_name = serializers.CharField(source='room.name', read_only=True, default='')
    
    staff_name = serializers.SerializerMethodField()
    staff_email = serializers.CharField(source='staff.email', read_only=True, default='')
    staff_image = serializers.SerializerMethodField()
    
    booked_count = serializers.SerializerMethodField()
    waitlist_count = serializers.SerializerMethodField()
    bookings = serializers.SerializerMethodField()
    user_booking_status = serializers.SerializerMethodField()

    class Meta:
        model = ClassSession
        fields = [
            'id', 'template', 'template_name', 'description', 'duration_min', 'category', 'intensity',
            'location_id', 'location_name', 'recurrence_rule', 'room', 'room_name',
            'staff', 'staff_name', 'staff_email', 'staff_image',
            'start_at', 'end_at', 'capacity', 'status', 'is_full',
            'booked_count', 'waitlist_count', 'bookings', 'user_booking_status',
            'created_at'
        ]
        read_only_fields = [
            'id', 'template_name', 'description', 'duration_min', 'category', 'intensity',
            'location_id', 'location_name', 'room_name', 'staff_name', 'staff_email', 'staff_image',
            'is_full', 'booked_count', 'waitlist_count', 'bookings', 'user_booking_status',
            'created_at'
        ]

    def get_staff_name(self, obj):
        if not obj.staff:
            return ""
        profile = getattr(obj.staff, 'profile', None)
        if profile:
            name = getattr(profile, 'nickname', None) or f"{getattr(profile, 'first_name', '')} {getattr(profile, 'last_name', '')}".strip()
            if name:
                return name
        return obj.staff.email

    def get_staff_image(self, obj):
        if not obj.staff:
            return None
        profile = getattr(obj.staff, 'profile', None)
        if profile and getattr(profile, 'profile_image', None) and hasattr(profile.profile_image, 'url'):
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(profile.profile_image.url)
            return profile.profile_image.url
        return None

    def get_booked_count(self, obj):
        if hasattr(obj, 'bookings'):
            return obj.bookings.filter(status='booked').count()
        return 0

    def get_waitlist_count(self, obj):
        if hasattr(obj, 'waitlist_entries'):
            return obj.waitlist_entries.filter(status='waiting').count()
        return 0

    def get_bookings(self, obj):
        request = self.context.get('request')
        if request and request.user and request.user.role in ['trainer', 'gym_owner', 'gym_manager', 'staff']:
            if hasattr(obj, 'bookings'):
                active_bookings = obj.bookings.filter(status__in=['booked', 'checked_in']).select_related('client', 'client__profile')
                results = []
                for b in active_bookings:
                    c = b.client
                    prof = getattr(c, 'profile', None)
                    name = getattr(prof, 'nickname', '') or f"{getattr(prof, 'first_name', '')} {getattr(prof, 'last_name', '')}".strip() or c.email
                    img = None
                    if prof and getattr(prof, 'profile_image', None) and hasattr(prof.profile_image, 'url'):
                        img = request.build_absolute_uri(prof.profile_image.url)
                    results.append({
                        "id": str(b.id),
                        "client_id": str(c.id),
                        "client_email": c.email,
                        "client_name": name,
                        "client_image": img,
                        "status": b.status,
                        "checked_in_at": b.checked_in_at,
                        "join_mode": b.join_mode,
                    })
                return results
        return []

    def get_user_booking_status(self, obj):
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            if hasattr(obj, 'bookings') and obj.bookings.filter(client=request.user, status='booked').exists():
                return "booked"
            if hasattr(obj, 'waitlist_entries') and obj.waitlist_entries.filter(client=request.user, status='waiting').exists():
                return "waitlist"
        return None

    def validate(self, data):
        start = data.get('start_at', self.instance.start_at if self.instance else None)
        end = data.get('end_at', self.instance.end_at if self.instance else None)
        if start and end and start >= end:
            raise serializers.ValidationError("End time must be after start time.")
        return data


class PackageTypeSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)

    class Meta:
        model = PackageType
        fields = ['id', 'location', 'location_name', 'name', 'credit_count', 'price', 'validity_days', 'created_at']
        read_only_fields = ['id', 'location_name', 'created_at']


class PackageSerializer(serializers.ModelSerializer):
    package_type_name = serializers.CharField(source='package_type.name', read_only=True)
    client_name = serializers.CharField(source='client.profile.nickname', read_only=True)
    client_email = serializers.CharField(source='client.email', read_only=True)

    class Meta:
        model = Package
        fields = [
            'id', 'client', 'client_name', 'client_email', 'package_type', 
            'package_type_name', 'credits_remaining', 'purchased_at', 'expires_at', 'created_at'
        ]
        read_only_fields = ['id', 'client_name', 'client_email', 'package_type_name', 'created_at']
        extra_kwargs = {
            'credits_remaining': {'required': False},
            'expires_at': {'required': False}
        }

    def create(self, validated_data):
        pkg_type = validated_data['package_type']
        if not validated_data.get('credits_remaining'):
            validated_data['credits_remaining'] = pkg_type.credit_count
        if not validated_data.get('expires_at'):
            validated_data['expires_at'] = timezone.now() + timedelta(days=pkg_type.validity_days)
        return super().create(validated_data)


class BookingCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ['id', 'session', 'join_mode', 'music_preference']

    def validate(self, data):
        session = data['session']
        if session.status != 'scheduled':
            raise serializers.ValidationError("Cannot book a session that is not in scheduled status.")
        return data


class BookingReadSerializer(serializers.ModelSerializer):
    session = ClassSessionSerializer(read_only=True)
    client_email = serializers.CharField(source='client.email', read_only=True)

    class Meta:
        model = Booking
        fields = ['id', 'client', 'client_email', 'session', 'status', 'credit_source', 'checked_in_at', 'join_mode', 'music_preference', 'created_at']


class BookingEditSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ['join_mode', 'music_preference', 'status', 'checked_in_at']


class AppointmentSerializer(serializers.ModelSerializer):
    client_email = serializers.CharField(source='client.email', read_only=True)
    provider_name = serializers.CharField(source='provider.profile.nickname', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    room_name = serializers.CharField(source='room.name', read_only=True)

    class Meta:
        model = Appointment
        fields = [
            'id', 'client', 'client_email', 'provider', 'provider_name', 
            'location', 'location_name', 'room', 'room_name', 
            'start_at', 'end_at', 'status', 'credit_source', 'created_at'
        ]
        read_only_fields = ['id', 'client', 'client_email', 'provider_name', 'location_name', 'room_name', 'created_at']

    def validate(self, data):
        start = data.get('start_at')
        end = data.get('end_at')
        if start >= end:
            raise serializers.ValidationError("End time must be after start time.")
        return data


class WaitlistSerializer(serializers.ModelSerializer):
    client_email = serializers.CharField(source='client.email', read_only=True)
    session_title = serializers.CharField(source='session.template.name', read_only=True)

    class Meta:
        model = Waitlist
        fields = ['id', 'client', 'client_email', 'session', 'session_title', 'position', 'status', 'offered_at', 'expires_at', 'created_at']
        read_only_fields = ['id', 'client_email', 'session_title', 'position', 'created_at']


class SubstituteRequestSerializer(serializers.ModelSerializer):
    session_details = ClassSessionSerializer(source='session', read_only=True)
    requested_by_email = serializers.CharField(source='requested_by_staff.email', read_only=True)
    accepted_by_email = serializers.CharField(source='accepted_by_staff.email', read_only=True)

    class Meta:
        model = SubstituteRequest
        fields = [
            'id', 'session', 'session_details', 'requested_by_staff', 'requested_by_email',
            'accepted_by_staff', 'accepted_by_email', 'status', 'created_at'
        ]
        read_only_fields = ['id', 'session_details', 'requested_by_email', 'accepted_by_email', 'created_at']


class PaymentSerializer(serializers.ModelSerializer):
    client_email = serializers.CharField(source='client.email', read_only=True)

    class Meta:
        model = Payment
        fields = ['id', 'client', 'client_email', 'amount', 'type', 'related_booking', 'status', 'provider_ref', 'idempotency_key', 'created_at']
        read_only_fields = ['id', 'client_email', 'created_at']


class CancellationPolicySerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source='template.name', read_only=True)

    class Meta:
        model = CancellationPolicy
        fields = ['id', 'scope_type', 'template', 'template_name', 'membership_tier', 'cutoff_hours', 'late_fee_amount', 'created_at']
        read_only_fields = ['id', 'template_name', 'created_at']


class NotificationSerializer(serializers.ModelSerializer):
    recipient_email = serializers.CharField(source='recipient.email', read_only=True)

    class Meta:
        model = Notification
        fields = ['id', 'recipient', 'recipient_email', 'channel', 'template_key', 'related_entity_id', 'sent_at', 'created_at']
        read_only_fields = ['id', 'recipient_email', 'created_at']


class StaffAssignClientSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.profile.nickname', read_only=True)
    client_name = serializers.CharField(source='client.profile.nickname', read_only=True)
    client_email = serializers.EmailField(source='client.email', read_only=True)

    class Meta:
        model = StaffClientAssignment
        fields = ['id', 'staff', 'client', 'staff_name', 'client_name', 'client_email']
        read_only_fields = ['id']

    def validate(self, data):
        if data['staff'].tenant != data['client'].tenant:
            raise serializers.ValidationError("Staff and Client must belong to the same gym.")
        return data

class FacilityAccessLogSerializer(serializers.ModelSerializer):
    client_email = serializers.CharField(source='client.email', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)

    class Meta:
        model = FacilityAccessLog
        fields = ['id', 'client', 'client_email', 'location', 'location_name', 'checked_in_at', 'checked_out_at']
        read_only_fields = ['id', 'checked_in_at', 'checked_out_at']

class PayoutRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayoutRun
        fields = ['id', 'status', 'total_amount', 'stripe_payout_id', 'created_at']
        read_only_fields = ['id', 'created_at']

class PlatformLedgerSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformLedger
        fields = ['id', 'payment', 'gross_amount', 'platform_fee', 'net_payout_amount', 'payout_run', 'created_at']
        read_only_fields = ['id', 'created_at']