from datetime import timedelta, datetime
from django.utils import timezone
from rest_framework import serializers
from django.db import transaction
from django.db.models import Q
from .models import (
    Location, Room, SpotType, RoomLayout, Spot, StaffLocation, StaffAvailability, ClassTemplate,
    RecurrenceRule, ClassSession, Booking, Appointment, Waitlist,
    SubstituteRequest, PackageType, Package, PackageGrantSource, Payment, CancellationPolicy,
    StaffClientAssignment, FacilityAccessLog, ClientBookingPreference,
    Event, EventSession, EventEnrollment, TenantBookingSettings
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
        extra_kwargs = {
            'location': {'required': False}
        }


class SpotTypeSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source='location.name', read_only=True)
    spots_count = serializers.SerializerMethodField()

    class Meta:
        model = SpotType
        fields = [
            'id', 'location', 'location_name', 'name', 'prefix',
            'is_bookable', 'color', 'spots_count', 'created_at'
        ]
        read_only_fields = ['id', 'location_name', 'spots_count', 'created_at']
        extra_kwargs = {
            'location': {'required': False}
        }

    def get_spots_count(self, obj):
        from .models import Spot
        return Spot.all_objects.filter(spot_type=obj).count()

    def validate(self, attrs):
        location = attrs.get('location') or (self.instance.location if self.instance else None)

        # Check maximum spot types per location
        if location and (not self.instance or self.instance.location != location):
            if SpotType.objects.filter(location=location).count() >= SpotType.MAX_PER_LOCATION:
                raise serializers.ValidationError(
                    {"detail": f"Maximum of {SpotType.MAX_PER_LOCATION} spot types allowed per location."}
                )

        # Prevent changing location if spots are already in use
        if self.instance and 'location' in attrs and attrs['location'] != self.instance.location:
            from .models import Spot
            if Spot.all_objects.filter(spot_type=self.instance).exists():
                raise serializers.ValidationError(
                    {"location": "Cannot change the location of a spot type that is already referenced by layout spots."}
                )

        # Case-insensitive name uniqueness within location
        name = attrs.get('name')
        if name and location:
            name_clean = name.strip()
            qs = SpotType.all_objects.filter(location=location, name__iexact=name_clean)
            if self.instance:
                qs = qs.exclude(id=self.instance.id)
            if qs.exists():
                raise serializers.ValidationError(
                    {"name": f"A spot type with the name '{name_clean}' already exists for this location."}
                )

        prefix = attrs.get('prefix')
        if prefix:
            attrs['prefix'] = prefix.strip()

        return attrs

    def update(self, instance, validated_data):
        old_prefix = instance.prefix
        instance = super().update(instance, validated_data)
        new_prefix = instance.prefix
        if old_prefix != new_prefix:
            from .models import Spot
            # Sync spot labels in layouts
            for spot in Spot.all_objects.filter(spot_type=instance):
                spot.label = f"{new_prefix}{spot.number}"
                spot.save(update_fields=['label'])
        return instance


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
        extra_kwargs = {
            'room': {'required': False}
        }

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

        if validated_data.get('layout') and not validated_data.get('layout_version'):
            validated_data['layout_version'] = validated_data['layout'].version

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
        if request and request.user and request.user.role in ['trainer', 'gym_owner', 'gym_manager', 'staff', 'front_desk']:
            if hasattr(obj, 'bookings'):
                active_bookings = obj.bookings.filter(status__in=['booked', 'checked_in', 'attended']).select_related('client', 'client__profile').order_by('-created_at')
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

        # Capacity validation against layout bookable spots
        capacity = data.get('capacity', self.instance.capacity if self.instance else None)
        if not capacity and data.get('template'):
            capacity = data['template'].default_capacity
        layout = data.get('layout') or (self.instance.layout if self.instance else None)
        if capacity and layout:
            from .services import validate_event_capacity
            from django.core.exceptions import ValidationError as DjangoValidationError
            try:
                validate_event_capacity(capacity=capacity, layout=layout)
            except DjangoValidationError as e:
                raise serializers.ValidationError({"capacity": e.messages if hasattr(e, 'messages') else str(e)})

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
    session = serializers.PrimaryKeyRelatedField(
        queryset=ClassSession.all_objects.all()
    )
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
    client_name = serializers.SerializerMethodField()
    client_first_name = serializers.CharField(source='client.first_name', read_only=True, default='')
    client_last_name = serializers.CharField(source='client.last_name', read_only=True, default='')
    session_name = serializers.CharField(source='session.template.name', read_only=True, default='')
    staff_name = serializers.SerializerMethodField()
    spot_label = serializers.CharField(source='spot.label', read_only=True, default=None)
    spot_number = serializers.IntegerField(source='spot.number', read_only=True, default=None)

    class Meta:
        model = Booking
        fields = [
            'id', 'client', 'client_email', 'client_name', 'client_first_name', 'client_last_name',
            'session', 'session_name', 'staff_name', 'spot', 'spot_label',
            'spot_number', 'is_guest', 'status', 'credit_source', 'credits_used', 'checked_in_at',
            'checked_out_at', 'join_mode', 'music_preference', 'created_at', 'updated_at'
        ]

    def get_client_name(self, obj):
        if not obj.client:
            return ""
        profile = getattr(obj.client, 'profile', None)
        if profile:
            full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip()
            if full_name:
                return full_name
            if profile.nickname:
                return profile.nickname
        user_name = f"{obj.client.first_name or ''} {obj.client.last_name or ''}".strip()
        if user_name:
            return user_name
        return obj.client.email.split('@')[0] if obj.client.email else ""

    def get_staff_name(self, obj):
        session = getattr(obj, 'session', None)
        staff = getattr(session, 'staff', None) if session else None
        if not staff:
            return ""
        profile = getattr(staff, 'profile', None)
        if profile:
            full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip()
            if full_name:
                return full_name
            if profile.nickname:
                return profile.nickname
        user_name = f"{staff.first_name or ''} {staff.last_name or ''}".strip()
        if user_name:
            return user_name
        return staff.email.split('@')[0] if staff.email else ""


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
    client_name = serializers.SerializerMethodField()
    client_image = serializers.SerializerMethodField()
    client_rx_level = serializers.SerializerMethodField()
    client_level = serializers.SerializerMethodField()
    provider_name = serializers.SerializerMethodField()
    staff_name = serializers.SerializerMethodField()
    staff_image = serializers.SerializerMethodField()
    provider_image = serializers.SerializerMethodField()
    location_name = serializers.CharField(source='location.name', read_only=True)
    room_name = serializers.CharField(source='room.name', read_only=True)

    class Meta:
        model = Appointment
        fields = [
            'id', 'client', 'client_email', 'client_name', 'client_image',
            'client_rx_level', 'client_level', 'provider', 'provider_name',
            'staff_name', 'staff_image', 'provider_image',
            'location', 'location_name', 'room', 'room_name',
            'start_at', 'end_at', 'status', 'credit_source', 'credits_used', 'created_at'
        ]
        read_only_fields = [
            'id', 'client_email', 'client_name', 'client_image',
            'client_rx_level', 'client_level', 'provider_name', 'staff_name',
            'staff_image', 'provider_image', 'location_name', 'room_name',
            'credits_used', 'created_at'
        ]

    def get_client_name(self, obj):
        if not obj.client:
            return ""
        profile = getattr(obj.client, 'profile', None)
        if profile:
            full_name = f"{profile.first_name or ''} {profile.last_name or ''}".strip()
            if full_name:
                return full_name
            if profile.nickname:
                return profile.nickname
        user_name = f"{obj.client.first_name or ''} {obj.client.last_name or ''}".strip()
        if user_name:
            return user_name
        return obj.client.email.split('@')[0] if obj.client.email else ""

    def get_client_image(self, obj):
        if not obj.client:
            return None
        profile = getattr(obj.client, 'profile', None)
        if profile and getattr(profile, 'profile_image', None) and hasattr(profile.profile_image, 'url'):
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(profile.profile_image.url)
            return profile.profile_image.url
        return None

    def get_client_rx_level(self, obj):
        if not obj.client:
            return "RX1"
        profile = getattr(obj.client, 'profile', None)
        if profile and getattr(profile, 'level', None):
            return profile.level
        return "RX1"

    def get_client_level(self, obj):
        return self.get_client_rx_level(obj)

    def get_provider_name(self, obj):
        staff = getattr(obj, 'provider', None) or getattr(obj, 'staff', None)
        if not staff:
            return ""
        profile = getattr(staff, 'profile', None)
        if profile:
            nickname = getattr(profile, 'nickname', None)
            if nickname:
                return nickname
            full_name = f"{getattr(profile, 'first_name', '')} {getattr(profile, 'last_name', '')}".strip()
            if full_name:
                return full_name
        return staff.email if staff else ""

    def get_staff_name(self, obj):
        return self.get_provider_name(obj)

    def get_staff_image(self, obj):
        staff = getattr(obj, 'provider', None) or getattr(obj, 'staff', None)
        if not staff:
            return None
        profile = getattr(staff, 'profile', None)
        if profile and getattr(profile, 'profile_image', None) and hasattr(profile.profile_image, 'url'):
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(profile.profile_image.url)
            return profile.profile_image.url
        return None

    def get_provider_image(self, obj):
        return self.get_staff_image(obj)

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
        start = data.get('start_at') or (self.instance.start_at if self.instance else None)
        end = data.get('end_at') or (self.instance.end_at if self.instance else None)
        if start and end and start >= end:
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


class ClientBookingPreferenceSerializer(serializers.ModelSerializer):
    remote_session = serializers.SerializerMethodField()
    client_email = serializers.EmailField(source='client.email', read_only=True)

    class Meta:
        model = ClientBookingPreference
        fields = [
            'id',
            'client',
            'client_email',
            'join_mode',
            'remote_session',
            'virtual_coach',
            'music_preference',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'client', 'client_email', 'remote_session', 'created_at', 'updated_at']

    def get_remote_session(self, obj):
        return obj.join_mode

    def to_internal_value(self, data):
        normalized = data.copy() if hasattr(data, 'copy') else dict(data)

        # 1. Map join_mode / remote_session / attendance_type
        for key in ['remote_session', 'remoteSession', 'Remote Session', 'attendance_type', 'attendanceType']:
            if key in normalized and 'join_mode' not in normalized:
                val = normalized.pop(key)
                if isinstance(val, bool):
                    normalized['join_mode'] = 'remote' if val else 'physical'
                else:
                    normalized['join_mode'] = str(val)

        # If join_mode itself is a boolean
        if 'join_mode' in normalized and isinstance(normalized['join_mode'], bool):
            normalized['join_mode'] = 'remote' if normalized['join_mode'] else 'physical'

        # 2. Map virtual_coach
        for key in ['virtualCoach', 'Virtual Coach', 'virtual_coaching']:
            if key in normalized and 'virtual_coach' not in normalized:
                val = normalized.pop(key)
                if isinstance(val, bool):
                    normalized['virtual_coach'] = 'virtual' if val else 'in_person'
                else:
                    normalized['virtual_coach'] = str(val)

        if 'virtual_coach' in normalized and isinstance(normalized['virtual_coach'], bool):
            normalized['virtual_coach'] = 'virtual' if normalized['virtual_coach'] else 'in_person'

        # 3. Map music_preference
        for key in ['musicPreference', 'Music Preference', 'music']:
            if key in normalized and 'music_preference' not in normalized:
                val = normalized.pop(key)
                if isinstance(val, bool):
                    normalized['music_preference'] = 'standard' if val else ''
                else:
                    normalized['music_preference'] = str(val)

        if 'music_preference' in normalized and isinstance(normalized['music_preference'], bool):
            normalized['music_preference'] = 'standard' if normalized['music_preference'] else ''

        return super().to_internal_value(normalized)


# ==============================================================================
# MINDBODY-STYLE EVENTS & WORKSHOPS SERIALIZERS
# ==============================================================================

def _format_user_name(user):
    if not user:
        return ""
    profile = getattr(user, 'profile', None)
    if profile:
        name = getattr(profile, 'nickname', '') or f"{getattr(profile, 'first_name', '')} {getattr(profile, 'last_name', '')}".strip()
        if name:
            return name
    return user.email


class EventSessionSerializer(serializers.ModelSerializer):
    room_name = serializers.CharField(source='room.name', read_only=True, default='')
    instructor_name = serializers.SerializerMethodField()

    class Meta:
        model = EventSession
        fields = [
            'id', 'event', 'session_number', 'title', 'start_at', 'end_at',
            'room', 'room_name', 'instructor', 'instructor_name', 'status', 'created_at'
        ]
        read_only_fields = ['id', 'room_name', 'instructor_name', 'created_at']

    def get_instructor_name(self, obj):
        return _format_user_name(obj.instructor)


class EventListSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    room_name = serializers.CharField(source='room.name', read_only=True, default='')
    primary_instructor_name = serializers.SerializerMethodField()
    my_enrollment = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            'id', 'title', 'category', 'category_display', 'description', 'image',
            'location', 'location_name', 'room', 'room_name',
            'primary_instructor', 'primary_instructor_name',
            'event_type', 'enrollment_type', 'start_at', 'end_at',
            'registration_opens_at', 'registration_closes_at',
            'capacity', 'waitlist_capacity', 'is_free', 'credits_required',
            'status', 'cancellation_cutoff_hours',
            'is_registration_open', 'enrolled_count', 'waitlist_count',
            'spots_remaining', 'is_full', 'is_waitlist_full',
            'my_enrollment', 'created_at'
        ]
        read_only_fields = [
            'id', 'category_display', 'location_name', 'room_name',
            'primary_instructor_name', 'is_registration_open', 'enrolled_count',
            'waitlist_count', 'spots_remaining', 'is_full', 'is_waitlist_full',
            'my_enrollment', 'created_at'
        ]

    def get_primary_instructor_name(self, obj):
        return _format_user_name(obj.primary_instructor)

    def get_my_enrollment(self, obj):
        request = self.context.get('request')
        if not request or not request.user or not request.user.is_authenticated:
            return None
        enrollment = obj.enrollments.filter(client=request.user).first()
        if not enrollment:
            return None
        return {
            "id": str(enrollment.id),
            "status": enrollment.status,
            "pricing_type": enrollment.pricing_type,
            "credits_deducted": enrollment.credits_deducted,
            "checked_in_at": enrollment.checked_in_at,
        }


class EventDetailSerializer(EventListSerializer):
    sessions = EventSessionSerializer(many=True, read_only=True)
    assistant_instructors_details = serializers.SerializerMethodField()

    class Meta(EventListSerializer.Meta):
        fields = EventListSerializer.Meta.fields + [
            'terms_and_conditions', 'assistant_instructors',
            'assistant_instructors_details', 'sessions'
        ]

    def get_assistant_instructors_details(self, obj):
        instructors = []
        for user in obj.assistant_instructors.all():
            instructors.append({
                "id": str(user.id),
                "name": _format_user_name(user),
                "email": user.email,
            })
        return instructors


class EventCreateUpdateSerializer(serializers.ModelSerializer):
    location = serializers.PrimaryKeyRelatedField(queryset=Location.all_objects.all())
    room = serializers.PrimaryKeyRelatedField(queryset=Room.all_objects.all(), required=False, allow_null=True)
    primary_instructor = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)
    assistant_instructors = serializers.PrimaryKeyRelatedField(many=True, queryset=User.objects.all(), required=False)

    sessions = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        write_only=True
    )

    class Meta:
        model = Event
        fields = [
            'id', 'title', 'category', 'description', 'image',
            'location', 'room', 'primary_instructor', 'assistant_instructors',
            'event_type', 'enrollment_type', 'start_at', 'end_at',
            'registration_opens_at', 'registration_closes_at',
            'capacity', 'waitlist_capacity', 'is_free', 'credits_required',
            'status', 'cancellation_cutoff_hours', 'terms_and_conditions',
            'sessions'
        ]
        read_only_fields = ['id']

    def to_internal_value(self, data):
        import json
        normalized = data.copy() if hasattr(data, 'copy') else dict(data)

        # Handle stringified JSON in multipart/form-data
        if 'sessions' in normalized and isinstance(normalized['sessions'], str):
            try:
                normalized['sessions'] = json.loads(normalized['sessions'])
            except Exception:
                pass

        if 'assistant_instructors' in normalized and isinstance(normalized['assistant_instructors'], str):
            try:
                normalized['assistant_instructors'] = json.loads(normalized['assistant_instructors'])
            except Exception:
                pass

        return super().to_internal_value(normalized)

    def validate(self, data):
        start_at = data.get('start_at', getattr(self.instance, 'start_at', None))
        end_at = data.get('end_at', getattr(self.instance, 'end_at', None))
        if start_at and end_at and end_at <= start_at:
            raise serializers.ValidationError({"end_at": "Event end time must be after start time."})

        reg_opens = data.get('registration_opens_at', getattr(self.instance, 'registration_opens_at', None))
        reg_closes = data.get('registration_closes_at', getattr(self.instance, 'registration_closes_at', None))
        if reg_opens and reg_closes and reg_closes <= reg_opens:
            raise serializers.ValidationError({"registration_closes_at": "Registration close time must be after open time."})

        is_free = data.get('is_free', getattr(self.instance, 'is_free', False))
        credits_required = data.get('credits_required', getattr(self.instance, 'credits_required', 1))
        if not is_free and credits_required < 1:
            raise serializers.ValidationError({"credits_required": "Paid events require at least 1 client pass credit."})

        capacity = data.get('capacity', getattr(self.instance, 'capacity', None))
        if capacity is not None and capacity <= 0:
            raise serializers.ValidationError({"capacity": "Capacity must be greater than zero."})

        return data

    def create(self, validated_data):
        from .event_services import create_event
        sessions_data = validated_data.pop('sessions', None)
        request = self.context.get('request')
        tenant = request.tenant if request and hasattr(request, 'tenant') else validated_data['location'].tenant
        return create_event(tenant=tenant, validated_data=validated_data, sessions_data=sessions_data)

    def update(self, instance, validated_data):
        assistant_instructors = validated_data.pop('assistant_instructors', None)
        sessions_data = validated_data.pop('sessions', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if assistant_instructors is not None:
            instance.assistant_instructors.set(assistant_instructors)

        if sessions_data is not None:
            instance.sessions.all().delete()
            request = self.context.get('request')
            tenant = request.tenant if request and hasattr(request, 'tenant') else instance.tenant
            for idx, s in enumerate(sessions_data, start=1):
                EventSession.objects.create(
                    tenant=tenant,
                    event=instance,
                    session_number=s.get('session_number', idx),
                    title=s.get('title', f"Session {idx}"),
                    start_at=s['start_at'],
                    end_at=s['end_at'],
                    room_id=s.get('room_id') or s.get('room'),
                    instructor_id=s.get('instructor_id') or s.get('instructor'),
                    status='scheduled'
                )
        return instance


class EventEnrollmentSerializer(serializers.ModelSerializer):
    event_title = serializers.CharField(source='event.title', read_only=True)
    event_category = serializers.CharField(source='event.category', read_only=True)
    event_start_at = serializers.DateTimeField(source='event.start_at', read_only=True)
    event_end_at = serializers.DateTimeField(source='event.end_at', read_only=True)
    location_name = serializers.CharField(source='event.location.name', read_only=True)
    client_name = serializers.SerializerMethodField()
    client_email = serializers.CharField(source='client.email', read_only=True)
    package_name = serializers.CharField(source='credit_source.package_type.name', read_only=True, default='')

    class Meta:
        model = EventEnrollment
        fields = [
            'id', 'event', 'event_title', 'event_category', 'event_start_at', 'event_end_at',
            'location_name', 'client', 'client_name', 'client_email',
            'status', 'pricing_type', 'credit_source', 'package_name',
            'credits_deducted', 'checked_in_at', 'cancelled_at',
            'cancellation_reason', 'notes', 'created_at'
        ]
        read_only_fields = [
            'id', 'event_title', 'event_category', 'event_start_at', 'event_end_at',
            'location_name', 'client_name', 'client_email', 'package_name',
            'pricing_type', 'credits_deducted', 'checked_in_at', 'cancelled_at',
            'created_at'
        ]

    def get_client_name(self, obj):
        return _format_user_name(obj.client)


class EventEnrollRequestSerializer(serializers.Serializer):
    client_id = serializers.UUIDField(required=False, help_text="Optional target client ID if staff is enrolling member")
    is_complimentary = serializers.BooleanField(default=False, help_text="Allow staff to grant complimentary entry")
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class EventRosterSerializer(serializers.ModelSerializer):
    client_id = serializers.UUIDField(source='client.id', read_only=True)
    client_name = serializers.SerializerMethodField()
    client_email = serializers.CharField(source='client.email', read_only=True)
    attended_sessions_ids = serializers.PrimaryKeyRelatedField(
        source='attended_sessions', many=True, read_only=True
    )

    class Meta:
        model = EventEnrollment
        fields = [
            'id', 'client_id', 'client_name', 'client_email',
            'status', 'pricing_type', 'credits_deducted',
            'checked_in_at', 'attended_sessions_ids', 'notes', 'created_at'
        ]

    def get_client_name(self, obj):
        return _format_user_name(obj.client)


class TenantBookingSettingsSerializer(serializers.ModelSerializer):
    session_late_cancellation = serializers.IntegerField(source='session_late_cancellation_hours', required=False, min_value=0)
    appointment_late_cancellation = serializers.IntegerField(source='appointment_late_cancellation_hours', required=False, min_value=0)

    class Meta:
        model = TenantBookingSettings
        fields = [
            'id', 'tenant',
            'session_late_cancellation_hours', 'session_booking_credits',
            'appointment_late_cancellation_hours', 'appointment_booking_credits',
            'session_late_cancellation', 'appointment_late_cancellation',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'tenant', 'created_at', 'updated_at']

    def to_internal_value(self, data):
        data = data.copy() if hasattr(data, 'copy') else dict(data)
        if 'session_late_cancellation' in data and 'session_late_cancellation_hours' not in data:
            data['session_late_cancellation_hours'] = data['session_late_cancellation']
        if 'appointment_late_cancellation' in data and 'appointment_late_cancellation_hours' not in data:
            data['appointment_late_cancellation_hours'] = data['appointment_late_cancellation']
        if 'session_credits' in data and 'session_booking_credits' not in data:
            data['session_booking_credits'] = data['session_credits']
        if 'appointment_credits' in data and 'appointment_booking_credits' not in data:
            data['appointment_booking_credits'] = data['appointment_credits']
        return super().to_internal_value(data)

    def validate_session_booking_credits(self, value):
        if value < 1:
            raise serializers.ValidationError("Session booking credits must be at least 1.")
        return value

    def validate_appointment_booking_credits(self, value):
        if value < 1:
            raise serializers.ValidationError("Appointment booking credits must be at least 1.")
        return value

    def validate_session_late_cancellation_hours(self, value):
        if value < 0:
            raise serializers.ValidationError("Session late cancellation period cannot be negative.")
        return value

    def validate_appointment_late_cancellation_hours(self, value):
        if value < 0:
            raise serializers.ValidationError("Appointment late cancellation period cannot be negative.")
        return value

        