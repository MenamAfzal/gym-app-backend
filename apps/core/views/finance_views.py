from django.db.models import Sum, Count, Q
from rest_framework import generics, serializers
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.payments.models import PlatformLedger, TenantPayout
from apps.scheduling.models import Payment
from apps.core.tenants.models import Tenant
from apps.core.views.platform_views import IsPlatformAdmin
from apps.core.tenants.context import bypass_tenant_isolation

class PlatformFinanceSummaryAPIView(generics.GenericAPIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request, *args, **kwargs):
        with bypass_tenant_isolation():
            
            ledgers = PlatformLedger.all_objects.all()
            total_revenue_gross = ledgers.aggregate(val=Sum('amount_gross'))['val'] or 0
            total_platform_fees = ledgers.aggregate(val=Sum('platform_fee'))['val'] or 0
            total_revenue_net = ledgers.aggregate(val=Sum('amount_net'))['val'] or 0

            payouts = TenantPayout.all_objects.all()
            pending_payouts = payouts.filter(status=TenantPayout.StatusChoices.PENDING).aggregate(val=Sum('amount'))['val'] or 0
            paid_payouts = payouts.filter(status=TenantPayout.StatusChoices.PAID).aggregate(val=Sum('amount'))['val'] or 0

            active_tenants = Tenant.objects.filter(is_active=True).count()
            
            unique_paying_users = Payment.all_objects.filter(status='completed').values('client').distinct().count()

            return Response({
                "total_revenue_gross": str(total_revenue_gross),
                "total_platform_fees": str(total_platform_fees),
                "total_revenue_net": str(total_revenue_net),
                "pending_payouts": str(pending_payouts),
                "paid_payouts": str(paid_payouts),
                "active_tenants": active_tenants,
                "unique_paying_users": unique_paying_users,
            })


class PlatformTransactionSerializer(serializers.ModelSerializer):
    client_name = serializers.SerializerMethodField()
    client_email = serializers.SerializerMethodField()
    tenant_name = serializers.CharField(source='tenant.name', read_only=True)
    
    class Meta:
        model = PlatformLedger
        fields = [
            'id', 'transaction_id', 'tenant_name', 'client_name', 'client_email',
            'amount_gross', 'platform_fee', 'amount_net', 'currency', 'type', 'status', 'created_at'
        ]

    def get_client_name(self, obj):
        payment = self.context.get('payments_map', {}).get(obj.transaction_id)
        if payment: 
            try:
                profile = payment.client.profile
                name = f"{profile.first_name} {profile.last_name}".strip()
                return name if name else payment.client.email
            except Exception:
                name = f"{payment.client.first_name} {payment.client.last_name}".strip()
                return name if name else payment.client.email
        
        if obj.type == 'sub' and obj.tenant:
            owner = obj.tenant.users.filter(role='tenant_admin').first()
            if owner:
                try:
                    profile = owner.profile
                    name = f"{profile.first_name} {profile.last_name}".strip()
                    return name if name else owner.email
                except Exception:
                    return owner.email
        return None

    def get_client_email(self, obj):
        payment = self.context.get('payments_map', {}).get(obj.transaction_id)
        if payment:
            return payment.client.email
            
        if obj.type == 'sub' and obj.tenant:
            owner = obj.tenant.users.filter(role='tenant_admin').first()
            if owner:
                return owner.email
        return None


class PlatformTransactionListView(generics.ListAPIView):
    permission_classes = [IsPlatformAdmin]
    serializer_class = PlatformTransactionSerializer
    
    def get_queryset(self):
        with bypass_tenant_isolation():
            return PlatformLedger.all_objects.select_related('tenant').order_by('-created_at')

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        
        items = page if page is not None else queryset
        
        transaction_ids = [item.transaction_id for item in items if item.transaction_id]
        
        with bypass_tenant_isolation():
            
            valid_uuids = []
            import uuid
            for tid in transaction_ids:
                try:
                    uuid.UUID(tid)
                    valid_uuids.append(tid)
                except ValueError:
                    pass
            
            payments = Payment.all_objects.filter(id__in=valid_uuids).select_related('client')
            payments_map = {str(p.id): p for p in payments}
            
        context = self.get_serializer_context()
        context['payments_map'] = payments_map
        
        serializer = self.get_serializer(items, many=True, context=context)
        
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)
