from rest_framework import serializers
from apps.payments.models import TenantCheckoutConfig
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
            'terms_url',
            'success_redirect_url',
            'cancel_redirect_url',
            'meta_pixel_id',
            'meta_capi_access_token',
            'is_meta_capi_configured',
            'meta_test_event_code',
            'embed_snippet',
        ]

    def get_is_meta_capi_configured(self, obj):
        return bool(obj.meta_capi_access_token)

    def get_available_packages(self, obj):
        tenant = obj.tenant
        packages = PackageType.all_objects.filter(
            tenant=tenant,
            is_active=True
        ).select_related('location').order_by('price')
        return [
            {
                'id': str(p.id),
                'name': p.name,
                'price': str(p.price),
                'location_name': p.location.name if p.location else '',
                'billing_cycle': p.billing_cycle,
                'credit_count': p.credit_count,
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

        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.save()
        return instance

