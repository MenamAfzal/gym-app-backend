from rest_framework import serializers
from apps.payments.models import (
    DEFAULT_HEALTH_COMMITMENT_BODY,
    DEFAULT_HEALTH_COMMITMENT_CHECKBOX_LABEL,
    DEFAULT_HEALTH_COMMITMENT_TITLE,
    HealthCommitmentSignature,
    SUPPORTED_STRIPE_CURRENCIES,
    TenantCheckoutConfig,
)
from apps.scheduling.models import PackageType


class TenantCheckoutConfigAdminSerializer(serializers.ModelSerializer):
    is_meta_capi_configured = serializers.SerializerMethodField()
    available_packages = serializers.SerializerMethodField()
    embed_snippet = serializers.SerializerMethodField()
    meta_capi_access_token = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=True,
        allow_null=True
    )
    health_commitment_version = serializers.IntegerField(read_only=True)

    class Meta:
        model = TenantCheckoutConfig
        fields = [
            'is_enabled',
            'allowed_origins',
            'enabled_package_ids',
            'available_packages',
            'theme_primary_color',
            'widget_layout',
            'widget_button_text',
            'show_location_filter',
            'require_phone_number',
            'require_health_commitment',
            'health_commitment_title',
            'health_commitment_body',
            'health_commitment_checkbox_label',
            'health_commitment_version',
            'terms_url',
            'success_redirect_url',
            'cancel_redirect_url',
            'meta_pixel_id',
            'meta_capi_access_token',
            'is_meta_capi_configured',
            'meta_test_event_code',
            'welcome_email_subject',
            'welcome_headline',
            'welcome_intro_text',
            'welcome_additional_benefits',
            'welcome_agreement_note',
            'welcome_body_text',
            'welcome_closing_text',
            'welcome_tagline',
            'welcome_reply_to_email',
            'currency',
            'embed_snippet',
        ]

    def validate(self, attrs):
        instance = self.instance
        require_hc = attrs.get(
            'require_health_commitment',
            instance.require_health_commitment if instance else True
        )
        title = attrs.get(
            'health_commitment_title',
            instance.health_commitment_title if instance else DEFAULT_HEALTH_COMMITMENT_TITLE
        )
        body = attrs.get(
            'health_commitment_body',
            instance.health_commitment_body if instance else DEFAULT_HEALTH_COMMITMENT_BODY
        )
        label = attrs.get(
            'health_commitment_checkbox_label',
            instance.health_commitment_checkbox_label if instance else DEFAULT_HEALTH_COMMITMENT_CHECKBOX_LABEL
        )
        if require_hc:
            if not (title and title.strip()):
                raise serializers.ValidationError({'health_commitment_title': 'Title is required when health commitment is enabled.'})
            if not (body and body.strip()):
                raise serializers.ValidationError({'health_commitment_body': 'Statement text is required when health commitment is enabled.'})
            if not (label and label.strip()):
                raise serializers.ValidationError({'health_commitment_checkbox_label': 'Checkbox label is required when health commitment is enabled.'})
        for field_name in (
            'welcome_email_subject',
            'welcome_headline',
            'welcome_intro_text',
            'welcome_body_text',
            'welcome_closing_text',
        ):
            if field_name in attrs and not (attrs[field_name] and attrs[field_name].strip()):
                raise serializers.ValidationError({field_name: 'This field cannot be blank.'})
        if 'currency' in attrs and attrs['currency']:
            clean_curr = str(attrs['currency']).strip().lower()
            valid_codes = {c['code'] for c in SUPPORTED_STRIPE_CURRENCIES}
            if clean_curr not in valid_codes:
                raise serializers.ValidationError({'currency': f"Currency '{clean_curr}' is not supported."})
            attrs['currency'] = clean_curr
        return attrs

    def get_is_meta_capi_configured(self, obj):
        return bool(obj.meta_capi_access_token)

    def get_available_packages(self, obj):
        tenant = obj.tenant
        packages = PackageType.all_objects.filter(
            tenant=tenant,
            is_active=True
        ).select_related('location').order_by('-is_featured', 'price')
        return [
            {
                'id': str(p.id),
                'name': p.name,
                'price': str(p.price),
                'location_name': p.location.name if p.location else '',
                'billing_cycle': p.billing_cycle,
                'credit_count': p.credit_count,
                'currency': (tenant.currency or 'usd'),
                'is_featured': p.is_featured,
                'feature_badge': p.feature_badge,
            }
            for p in packages
        ]

    def get_embed_snippet(self, obj):
        request = self.context.get('request')
        base_url = 'https://api.fitverx.com'
        if request:
            base_url = request.build_absolute_uri('/')[:-1]
        tenant_id = str(obj.tenant_id)
        return (
            f'<div id="fitverx-checkout-widget" data-tenant-id="{tenant_id}"></div>\n'
            f'<script src="{base_url}/static/checkout/fitverx-widget.js" async></script>'
        )

    def update(self, instance, validated_data):
        meta_token = validated_data.pop('meta_capi_access_token', None)
        if meta_token is not None:
            if meta_token.strip():
                instance.meta_capi_access_token = meta_token.strip()
            else:
                instance.meta_capi_access_token = None

        enabled_packages = validated_data.get('enabled_package_ids')
        if enabled_packages is not None:
            tenant_packages = set(
                PackageType.all_objects.filter(tenant=instance.tenant).values_list('id', flat=True)
            )
            validated_packages = [
                str(pid) for pid in enabled_packages if str(pid) in {str(tp) for tp in tenant_packages}
            ]
            validated_data['enabled_package_ids'] = validated_packages

        previous_currency = instance.currency
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        if 'currency' in validated_data and validated_data['currency'] != previous_currency:
            from apps.payments.stripe_package_service import StripePackageService
            StripePackageService.sync_all_tenant_packages_to_stripe(instance.tenant)

        return instance


class HealthCommitmentSignatureSerializer(serializers.ModelSerializer):
    checkout_session_id = serializers.UUIDField(source='checkout_session.id', read_only=True)
    user_id = serializers.UUIDField(source='user.id', read_only=True)

    class Meta:
        model = HealthCommitmentSignature
        fields = [
            'id',
            'user_id',
            'checkout_session_id',
            'signer_name',
            'signer_email',
            'statement_version',
            'statement_title',
            'statement_body',
            'checkbox_label',
            'accepted',
            'signed_at',
            'ip_address',
            'source',
        ]
        read_only_fields = fields

