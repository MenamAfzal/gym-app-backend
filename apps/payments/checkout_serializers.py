from rest_framework import serializers
from apps.scheduling.models import PackageType
from apps.payments.models import ExternalCheckoutSession, TenantCheckoutConfig
from apps.core.tenants.models import Tenant


class PublicGymInfoSerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()
    currency = serializers.CharField(default='usd', read_only=True)
    available_payment_methods = serializers.SerializerMethodField()
    checkout_settings = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = [
            'id',
            'name',
            'subdomain',
            'logo_url',
            'branding',
            'currency',
            'available_payment_methods',
            'checkout_settings',
        ]

    def get_logo_url(self, obj):
        if obj.logo:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.logo.url)
            return obj.logo.url
        return None

    def get_available_payment_methods(self, obj):
        return ['card', 'apple_pay', 'google_pay']

    def get_checkout_settings(self, obj):
        config = getattr(obj, 'checkout_config', None)
        return {
            'require_phone_number': config.require_phone_number if config else False,
            'terms_url': config.terms_url if config else None,
            'privacy_policy_url': getattr(config, 'privacy_policy_url', None) if config else None,
            'success_redirect_url': config.success_redirect_url if config else None,
            'cancel_redirect_url': config.cancel_redirect_url if config else None,
            'theme_primary_color': config.theme_primary_color if config else '#000000',
            'meta_pixel_id': config.meta_pixel_id if config else None,
        }


class PublicPackageSerializer(serializers.ModelSerializer):
    description = serializers.SerializerMethodField()
    location_id = serializers.UUIDField(source='location.id', read_only=True)
    location_name = serializers.CharField(source='location.name', read_only=True)
    location_address = serializers.CharField(source='location.address', read_only=True)
    currency = serializers.CharField(default='usd', read_only=True)

    class Meta:
        model = PackageType
        fields = [
            'id',
            'name',
            'description',
            'price',
            'currency',
            'billing_cycle',
            'credit_count',
            'validity_days',
            'location_id',
            'location_name',
            'location_address',
        ]

    def get_description(self, obj):
        return f"{obj.credit_count} credits valid for {obj.validity_days} days"


class CreateCheckoutSessionSerializer(serializers.Serializer):
    tenant_id = serializers.CharField()
    package_type_id = serializers.UUIDField()
    email = serializers.EmailField()
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    phone_number = serializers.CharField(max_length=25, required=False, allow_blank=True, default='')
    fbp = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    fbc = serializers.CharField(max_length=255, required=False, allow_blank=True, default='')
    source_url = serializers.URLField(max_length=500, required=False, allow_blank=True, default='')

    def validate_email(self, value):
        return value.strip().lower()


class VerifySessionOTPSerializer(serializers.Serializer):
    otp_code = serializers.CharField(max_length=10)

    def validate_otp_code(self, value):
        return value.strip()


class SetCustomerPasswordSerializer(serializers.Serializer):
    new_password = serializers.CharField(min_length=8, write_only=True)


class CheckoutSessionDetailSerializer(serializers.ModelSerializer):
    session_id = serializers.UUIDField(source='id', read_only=True)
    masked_email = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    tenant = serializers.SerializerMethodField()
    package = serializers.SerializerMethodField()
    pricing = serializers.SerializerMethodField()
    available_payment_methods = serializers.SerializerMethodField()
    tracking = serializers.SerializerMethodField()

    class Meta:
        model = ExternalCheckoutSession
        fields = [
            'session_id',
            'status',
            'payment_status',
            'is_verified',
            'can_proceed',
            'expires_at',
            'masked_email',
            'customer',
            'tenant',
            'package',
            'pricing',
            'available_payment_methods',
            'tracking',
        ]

    def get_masked_email(self, obj):
        email = obj.email or ''
        if '@' not in email:
            return email
        name, domain = email.split('@', 1)
        if len(name) <= 2:
            masked_name = name[0] + '*'
        else:
            masked_name = name[0] + ('*' * (len(name) - 2)) + name[-1]
        return f"{masked_name}@{domain}"

    def get_customer(self, obj):
        return {
            'email': obj.email,
            'masked_email': self.get_masked_email(obj),
            'first_name': obj.first_name,
            'last_name': obj.last_name,
            'phone_number': obj.phone_number,
        }

    def get_tenant(self, obj):
        tenant = obj.tenant
        logo_url = None
        if tenant and tenant.logo:
            request = self.context.get('request')
            if request:
                logo_url = request.build_absolute_uri(tenant.logo.url)
            else:
                logo_url = tenant.logo.url

        config = getattr(tenant, 'checkout_config', None)
        return {
            'id': str(tenant.id) if tenant else None,
            'name': tenant.name if tenant else '',
            'subdomain': tenant.subdomain if tenant else '',
            'logo_url': logo_url,
            'primary_color': config.theme_primary_color if config else '#000000',
            'terms_url': config.terms_url if config else None,
        }

    def get_package(self, obj):
        pkg = obj.package_type
        return {
            'id': str(pkg.id) if pkg else None,
            'name': pkg.name if pkg else '',
            'price': str(obj.price_snapshot),
            'currency': obj.currency,
            'billing_cycle': obj.billing_cycle_snapshot,
            'credit_count': obj.credit_count_snapshot,
            'validity_days': obj.validity_days_snapshot,
            'location_name': pkg.location.name if (pkg and pkg.location) else '',
        }

    def get_pricing(self, obj):
        return {
            'subtotal': str(obj.price_snapshot),
            'tax': '0.00',
            'total': str(obj.price_snapshot),
            'currency': obj.currency,
        }

    def get_available_payment_methods(self, obj):
        return ['card', 'apple_pay', 'google_pay']

    def get_tracking(self, obj):
        return {
            'initiate_event_id': str(obj.initiate_event_id),
            'purchase_event_id': str(obj.purchase_event_id),
        }


class CheckoutCompletionSerializer(serializers.ModelSerializer):
    session_id = serializers.UUIDField(source='id', read_only=True)
    membership = serializers.SerializerMethodField()
    customer = serializers.SerializerMethodField()
    mobile_app = serializers.SerializerMethodField()

    class Meta:
        model = ExternalCheckoutSession
        fields = [
            'session_id',
            'payment_status',
            'membership',
            'customer',
            'mobile_app',
        ]

    def get_membership(self, obj):
        pkg = obj.created_package
        if not pkg:
            return None
        return {
            'id': str(pkg.id),
            'status': pkg.status,
            'package_name': pkg.package_type.name if pkg.package_type else obj.package_type.name,
            'credits_allocated': pkg.total_credits_allocated,
            'credits_remaining': pkg.credits_remaining,
            'purchased_at': pkg.purchased_at,
            'expires_at': pkg.expires_at,
        }

    def get_customer(self, obj):
        return {
            'account_status': 'new_account' if obj.is_new_customer else 'existing_account',
            'email': obj.email,
            'password_setup_required': obj.is_new_customer and not (obj.customer_user and obj.customer_user.has_usable_password()),
        }

    def get_mobile_app(self, obj):
        tenant_id = str(obj.tenant_id)
        email = obj.email
        return {
            'download_url_ios': 'https://apps.apple.com/app/fitverx/id123456789',
            'download_url_android': 'https://play.google.com/store/apps/details?id=com.fitverx.app',
            'deep_link': f'fitverx://login?tenant_id={tenant_id}&email={email}',
            'instructions': 'Install the Fitverx mobile app and sign in with your email to book classes and view your membership.',
        }
