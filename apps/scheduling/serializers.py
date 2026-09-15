from datetime import timedelta, datetime
from django.utils import timezone
from rest_framework import serializers
from django.db import transaction
from django.db.models import Q
from .models import (
    Location, Room, SpotType, RoomLayout, Spot, StaffLocation, StaffAvailability, ClassTemplate,
    RecurrenceRule, ClassSession, Booking, Appointment, Waitlist,
    SubstituteRequest, PackageType, Package, PackageGrantSource, Payment, CancellationPolicy,
    StaffClientAssignment, FacilityAccessLog
)
from apps.users.models import User, UserRole

class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ['id', 'name', 'address', 'timezone', 'phone', 'created_at']
        read_only_fields = ['id', 'created_at']


class RoomSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)

    class Meta:
        model = Room
        fields = [
            'id', 'location', 'location_name', 'name', 'room_type',
            'capacity', 'default_capacity', 'description', 'image',
            'is_active', 'is_deleted', 'equipment_tags', 'created_at'
        ]
        read_only_fields = ['id', 'location_name', 'created_at', 'is_deleted']


class SpotTypeSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)

    class Meta:
        model = SpotType
        fields = ['id', 'location', 'location_name', 'name', 'prefix', 'is_bookable', 'color', 'created_at']
        read_only_fields = ['id', 'location_name', 'created_at']

    def validate(self, attrs):
        location = attrs.get('location') or (self.instance.location if self.instance else None)
        if not self.instance and location:
            if SpotType.objects.filter(location=location).count() >= SpotType.MAX_PER_LOCATION:
                raise serializers.ValidationError(
                    {"detail": f"Maximum of {SpotType.MAX_PER_LOCATION} spot types allowed per location."}
                )
        return attrs


class SpotInputSerializer(serializers.Serializer):
    row = serializers.IntegerField(min_value=0)
    col = serializers.IntegerField(min_value=0)
    spot_type = serializers.UUIDField(required=True)
    is_blocked = serializers.BooleanField(default=False, required=False)


class SpotSerializer(serializers.ModelSerializer):
    spot_type_name = serializers.CharField(source='spot_type.name', read_only=True)
    spot_type_prefix = serializers.CharField(source='spot_type.prefix', read_only=True)
    spot_type_color = serializers.CharField(source='spot_type.color', read_only=True)
    is_bookable = serializers.BooleanField(source='spot_type.is_bookable', read_only=True)

    class Meta:
        model = Spot
        fields = [
            'id', 'row', 'col', 'number', 'label', 'spot_type',
            'spot_type_name', 'spot_type_prefix', 'spot_type_color',
            'is_bookable', 'is_blocked'
        ]
        read_only_fields = ['id', 'number', 'label']


class RoomLayoutSerializer(serializers.ModelSerializer):
    spots = SpotSerializer(many=True, read_only=True)
    capacity = serializers.IntegerField(read_only=True)
    room_name = serializers.CharField(source='room.name', read_only=True)

    class Meta:
        model = RoomLayout
        fields = [
            'id', 'room', 'room_name', 'name', 'grid_rows', 'grid_cols',
            'capacity', 'version', 'is_deleted', 'spots', 'created_at'
        ]
        read_only_fields = ['id', 'room_name', 'capacity', 'version', 'is_deleted', 'created_at']


class RoomLayoutCreateSerializer(serializers.ModelSerializer):
    spots = SpotInputSerializer(many=True, required=False, default=list)

    class Meta:
        model = RoomLayout
        fields = ['id', 'room', 'name', 'grid_rows', 'grid_cols', 'spots']
        read_only_fields = ['id']

    def validate_spots(self, spots):
        seen = set()
        for s in spots:
            coord = (s['row'], s['col'])
            if coord in seen:
                raise serializers.ValidationError(f"Duplicate spot position at row {s['row']}, col {s['col']}.")
            seen.add(coord)
        return spots


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
    layout_name = serializers.CharField(source='layout.name', read_only=True, default='')
    
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
            'layout', 'layout_name', 'layout_version', 'blocked_spots',
            'staff', 'staff_name', 'staff_email', 'staff_image',
            'start_at', 'end_at', 'capacity', 'status', 'is_full',
            'booked_count', 'waitlist_count', 'bookings', 'user_booking_status',
            'created_at'
        ]
        read_only_fields = [
            'id', 'template_name', 'description', 'duration_min', 'category', 'intensity',
            'location_id', 'location_name', 'room_name', 'layout_name', 'staff_name', 'staff_email', 'staff_image',
            'is_full', 'booked_count', 'waitlist_count', 'bookings', 'user_booking_status',
            'created_at'
        ]

    def validate(self, attrs):
        capacity = attrs.get('capacity')
        layout = attrs.get('layout') or (self.instance.layout if self.instance else None)
        if capacity and layout:
            from .services import validate_event_capacity
            validate_event_capacity(capacity=capacity, layout=layout)
        return attrs

    def to_representation(self, instance):
        now = timezone.now()
        is_past = (instance.end_at and instance.end_at <= now) or (not instance.end_at and instance.start_at and instance.start_at <= now)
        if instance.status == 'scheduled' and is_past:
            instance.status = 'completed'
            if getattr(instance, 'pk', None):
                ClassSession.all_objects.filter(id=instance.id, status='scheduled').update(status='completed')
        return super().to_representation(instance)

    def create(self, validated_data):
        start_at = validated_data.get('start_at')
        end_at = validated_data.get('end_at')
        template = validated_data.get('template')

        if not end_at and template and start_at:
            duration = template.duration_min or 60
            end_at = start_at + timedelta(minutes=duration)
            validated_data['end_at'] = end_at

        if not validated_data.get('capacity') and template:
            validated_data['capacity'] = template.default_capacity

        now = timezone.now()
        is_past = (end_at and end_at <= now) or (not end_at and start_at and start_at <= now)
        if is_past and validated_data.get('status', 'scheduled') == 'scheduled':
            validated_data['status'] = 'completed'

        request = self.context.get('request')
        if request and not validated_data.get('tenant'):
            validated_data['tenant'] = getattr(request, 'tenant', None) or (request.user.tenant if getattr(request, 'user', None) else None)

        return super().create(validated_data)

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
            return obj.bookings.filter(status__in=['booked', 'checked_in', 'attended']).count()
        return 0

    def get_waitlist_count(self, obj):
        if hasattr(obj, 'waitlist_entries'):
            return obj.waitlist_entries.filter(status='waiting').count()
        return 0

    def get_bookings(self, obj):
        request = self.context.get('request')
        if request and request.user and request.user.role in ['trainer', 'gym_owner', 'gym_manager', 'staff']:
            if hasattr(obj, 'bookings'):
                active_bookings = obj.bookings.filter(status__in=['booked', 'checked_in', 'attended']).select_related('client', 'client__profile')
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
            if hasattr(obj, 'bookings'):
                user_booking = obj.bookings.filter(client=request.user, status__in=['booked', 'checked_in', 'attended']).first()
                if user_booking:
                    return user_booking.status
            waitlist_qs = getattr(obj, 'waitlists', None) or getattr(obj, 'waitlist_entries', None)
            if waitlist_qs and waitlist_qs.filter(client=request.user, status='waiting').exists():
                return "waitlist"
        return None

    def validate(self, data):
        start = data.get('start_at', self.instance.start_at if self.instance else None)
        end = data.get('end_at', self.instance.end_at if self.instance else None)
        if start and end and start >= end:
            raise serializers.ValidationError("End time must be after start time.")

        if 'staff' in data:
            staff = data['staff']
        elif self.instance:
            staff = self.instance.staff
        else:
            staff = None

        if 'room' in data:
            room = data['room']
        elif self.instance:
            room = self.instance.room
        else:
            room = None

        if 'status' in data:
            session_status = data['status']
        elif self.instance:
            session_status = self.instance.status
        else:
            session_status = 'scheduled'

        if staff and start and end and session_status != 'cancelled':
            staff_conflict_qs = ClassSession.objects.filter(
                staff=staff,
                start_at__lt=end,
                end_at__gt=start,
            ).exclude(status='cancelled')
            if self.instance:
                staff_conflict_qs = staff_conflict_qs.exclude(id=self.instance.id)

            if staff_conflict_qs.exists():
                raise serializers.ValidationError({
                    "staff": "This staff member is already assigned to another session at the same time slot."
                })

            appointment_conflict_qs = Appointment.objects.filter(
                provider=staff,
                start_at__lt=end,
                end_at__gt=start,
            ).exclude(status='cancelled')
            if appointment_conflict_qs.exists():
                raise serializers.ValidationError({
                    "staff": "This staff member has a conflicting private appointment at the same time slot."
                })

        if room and start and end and session_status != 'cancelled':
            room_conflict_qs = ClassSession.objects.filter(
                room=room,
                start_at__lt=end,
                end_at__gt=start,
            ).exclude(status='cancelled')
            if self.instance:
                room_conflict_qs = room_conflict_qs.exclude(id=self.instance.id)

            if room_conflict_qs.exists():
                raise serializers.ValidationError({
                    "room": "This room is already reserved for another session at the same time slot."
                })

        return data


class PackageTypeSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    subscriber_count = serializers.SerializerMethodField()

    class Meta:
        model = PackageType
        fields = [
            'id', 'location', 'location_name', 'name', 'credit_count', 
            'price', 'validity_days', 'billing_cycle', 'is_active',
            'stripe_product_id', 'stripe_price_id', 'subscriber_count', 'created_at'
        ]
        read_only_fields = ['id', 'location_name', 'stripe_product_id', 'stripe_price_id', 'subscriber_count', 'created_at']

    def get_subscriber_count(self, obj):
        return obj.purchased_packages.filter(status='active').count()

    def create(self, validated_data):
        from apps.payments.stripe_package_service import StripePackageService
        instance = super().create(validated_data)
        StripePackageService.sync_package_to_stripe(instance)
        return instance

    def update(self, instance, validated_data):
        from apps.payments.stripe_package_service import StripePackageService
        instance = super().update(instance, validated_data)
        StripePackageService.sync_package_to_stripe(instance)
        return instance


class PackageSerializer(serializers.ModelSerializer):
    package_type_name = serializers.CharField(source='package_type.name', read_only=True)
    client_name = serializers.CharField(source='client.profile.nickname', read_only=True)
    client_email = serializers.CharField(source='client.email', read_only=True)
    location = LocationSerializer(source='package_type.location', read_only=True)
    is_canceled = serializers.SerializerMethodField()
    assigned_by_email = serializers.CharField(source='assigned_by.email', read_only=True)

    class Meta:
        model = Package
        fields = [
            'id', 'client', 'client_name', 'client_email', 'package_type', 
            'package_type_name', 'credits_remaining', 'purchased_at', 'expires_at', 'created_at',
            'location', 'status', 'cancel_at_period_end', 'is_canceled',
            'grant_source', 'is_complimentary', 'assigned_by', 'assigned_by_email', 'price'
        ]
        read_only_fields = [
            'id', 'client_name', 'client_email', 'package_type_name', 'created_at',
            'location', 'is_canceled', 'grant_source', 'is_complimentary', 'assigned_by', 'assigned_by_email'
        ]
        extra_kwargs = {
            'credits_remaining': {'required': False},
            'expires_at': {'required': False},
            'price': {'required': False}
        }

    def get_is_canceled(self, obj):
        return obj.status == 'canceled' or obj.cancel_at_period_end

    def validate(self, attrs):
        request = self.context.get('request')
        client = attrs.get('client')
        package_type = attrs.get('package_type')
        credits_remaining = attrs.get('credits_remaining')
        expires_at = attrs.get('expires_at')

        # Only apply manual assignment validations on creation
        if not self.instance:
            tenant = getattr(request, 'tenant', None) if request else None
            if not tenant and client:
                tenant = client.tenant

            if not client:
                raise serializers.ValidationError({"client": "Client is required."})
            if not package_type:
                raise serializers.ValidationError({"package_type": "Package type is required."})

            # Anti-Fraud 1: Tenant Isolation
            if tenant:
                if client.tenant_id != tenant.id:
                    raise serializers.ValidationError({"client": "Target client does not belong to your gym."})
                if package_type.tenant_id != tenant.id:
                    raise serializers.ValidationError({"package_type": "Package type does not belong to your gym."})

            # Anti-Fraud 2: Target user must be an active CLIENT
            if client.role != UserRole.CLIENT:
                raise serializers.ValidationError({"client": "Packages can only be assigned to clients."})
            if not client.is_active:
                raise serializers.ValidationError({"client": "Cannot assign a package to an inactive client."})

            # Anti-Fraud 3: PackageType must be active
            if not package_type.is_active:
                raise serializers.ValidationError({"package_type": "This package type is inactive."})

            # Anti-Fraud 4: Strict finite credit limit (no unlimited / 0 / negative / excessive credits)
            if package_type.credit_count <= 0:
                raise serializers.ValidationError({"package_type": "Package type must have a positive credit count."})

            if credits_remaining is not None:
                if credits_remaining <= 0:
                    raise serializers.ValidationError({"credits_remaining": "Credits remaining must be greater than zero."})
                if credits_remaining > package_type.credit_count:
                    raise serializers.ValidationError({
                        "credits_remaining": f"Credits cannot exceed the package type limit of {package_type.credit_count}."
                    })

            # Anti-Fraud 5: Expiration date validation (finite duration)
            if expires_at is not None:
                if expires_at <= timezone.now():
                    raise serializers.ValidationError({"expires_at": "Expiration date must be in the future."})
                max_validity = timezone.now() + timedelta(days=max(package_type.validity_days * 2, 365))
                if expires_at > max_validity:
                    raise serializers.ValidationError({"expires_at": "Expiration date exceeds the maximum allowable validity period."})

            if request and tenant:
                now = timezone.now()
                month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

                monthly_tenant_free_count = Package.objects.filter(
                    tenant=tenant,
                    is_complimentary=True,
                    assigned_by__isnull=False,
                    created_at__gte=month_start
                ).exclude(
                    grant_source__in=[
                        PackageGrantSource.REWARD_RULE,
                        PackageGrantSource.REWARD_REDEMPTION,
                    ]
                ).count()

                if monthly_tenant_free_count >= 3:
                    raise serializers.ValidationError({
                        "detail": "Monthly limit of 3 free package assignments for this gym has been reached for this month."
                    })

                monthly_client_free_count = Package.objects.filter(
                    tenant=tenant,
                    client=client,
                    is_complimentary=True,
                    assigned_by__isnull=False,
                    created_at__gte=month_start
                ).exclude(
                    grant_source__in=[
                        PackageGrantSource.REWARD_RULE,
                        PackageGrantSource.REWARD_REDEMPTION,
                    ]
                ).count()

                if monthly_client_free_count >= 3:
                    raise serializers.ValidationError({
                        "detail": "This client has already received the maximum of 3 free packages for this month."
                    })

        return attrs

    def create(self, validated_data):
        request = self.context.get('request')
        pkg_type = validated_data['package_type']
        if not validated_data.get('credits_remaining'):
            validated_data['credits_remaining'] = pkg_type.credit_count
        if not validated_data.get('expires_at'):
            validated_data['expires_at'] = timezone.now() + timedelta(days=pkg_type.validity_days)

        # Mark as complimentary/free manual assignment
        validated_data['is_complimentary'] = True
        validated_data['grant_source'] = PackageGrantSource.MANUAL_COMPLIMENTARY
        if validated_data.get('price') is None:
            from decimal import Decimal
            validated_data['price'] = Decimal('0.00')

        if request and request.user:
            validated_data['assigned_by'] = request.user
            if not validated_data.get('tenant'):
                validated_data['tenant'] = getattr(request, 'tenant', None) or request.user.tenant

        return super().create(validated_data)


class BookingCreateSerializer(serializers.ModelSerializer):
    client = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True
    )
    spot = serializers.PrimaryKeyRelatedField(
        queryset=Spot.all_objects.all(),
        required=False,
        allow_null=True
    )
    is_guest = serializers.BooleanField(required=False, default=False)

    class Meta:
        model = Booking
        fields = ['id', 'session', 'join_mode', 'music_preference', 'client', 'spot', 'is_guest']
        validators = []  # Clear default UniqueTogetherValidator to prevent DRF from requiring client field

    def validate(self, data):
        session = data['session']
        if session.status != 'scheduled':
            raise serializers.ValidationError("Cannot book a session that is not in scheduled status.")

        spot = data.get('spot')
        if spot:
            if not session.layout:
                raise serializers.ValidationError({"spot": "This class does not have an assigned room layout."})
            if spot.layout_id != session.layout_id:
                raise serializers.ValidationError({"spot": "Selected spot does not belong to this class's room layout."})
            if not spot.spot_type.is_bookable:
                raise serializers.ValidationError({"spot": "Selected spot is non-bookable."})
            if spot.is_blocked:
                raise serializers.ValidationError({"spot": "Selected spot is blocked."})
            blocked_list = [str(b) for b in (session.blocked_spots or [])]
            if str(spot.id) in blocked_list:
                raise serializers.ValidationError({"spot": "Selected spot is blocked for this class occurrence."})

        return data


class BookingReadSerializer(serializers.ModelSerializer):
    session = ClassSessionSerializer(read_only=True)
    client_email = serializers.CharField(source='client.email', read_only=True)
    spot_label = serializers.CharField(source='spot.label', read_only=True, default=None)
    spot_number = serializers.IntegerField(source='spot.number', read_only=True, default=None)

    class Meta:
        model = Booking
        fields = [
            'id', 'client', 'client_email', 'session', 'spot', 'spot_label',
            'spot_number', 'is_guest', 'status', 'credit_source', 'checked_in_at',
            'join_mode', 'music_preference', 'created_at'
        ]


class BookingChangeSpotSerializer(serializers.Serializer):
    spot_id = serializers.UUIDField(required=True)


class BookingEditSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ['join_mode', 'music_preference', 'status', 'checked_in_at']


class AppointmentSerializer(serializers.ModelSerializer):
    client = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True
    )
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
        read_only_fields = ['id', 'client_email', 'provider_name', 'location_name', 'room_name', 'created_at']

    def to_representation(self, instance):
        now = timezone.now()
        is_past = (instance.end_at and instance.end_at <= now) or (not instance.end_at and instance.start_at and instance.start_at <= now)
        if instance.status == 'scheduled' and is_past:
            instance.status = 'completed'
            if getattr(instance, 'pk', None):
                Appointment.all_objects.filter(id=instance.id, status='scheduled').update(status='completed')
        return super().to_representation(instance)

    def create(self, validated_data):
        start_at = validated_data.get('start_at')
        end_at = validated_data.get('end_at')
        now = timezone.now()
        is_past = (end_at and end_at <= now) or (not end_at and start_at and start_at <= now)
        if is_past and validated_data.get('status', 'scheduled') == 'scheduled':
            validated_data['status'] = 'completed'

        request = self.context.get('request')
        if request and not validated_data.get('tenant'):
            validated_data['tenant'] = getattr(request, 'tenant', None) or (request.user.tenant if getattr(request, 'user', None) else None)

        return super().create(validated_data)

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
    client_name = serializers.SerializerMethodField()
    location_name = serializers.CharField(source='location.name', read_only=True)

    class Meta:
        model = FacilityAccessLog
        fields = ['id', 'client', 'client_name', 'client_email', 'location', 'location_name', 'checked_in_at', 'checked_out_at']
        read_only_fields = ['id', 'checked_in_at', 'checked_out_at']

    def get_client_name(self, obj):
        try:
            profile = obj.client.profile
            name = f"{profile.first_name} {profile.last_name}".strip()
            return name if name else obj.client.email
        except Exception:
            return obj.client.email