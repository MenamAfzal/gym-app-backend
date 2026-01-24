"""
Tenant Middleware

Resolves tenant from request subdomain and sets request.tenant.
All requests are tenant-scoped through this middleware.
"""
from django.http import Http404, HttpResponseForbidden
from django.conf import settings
from django.utils.deprecation import MiddlewareMixin
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant, reset_current_tenant

class TenantMiddleware(MiddlewareMixin):
    """
    Middleware to resolve tenant from request subdomain.
    """
    
    def process_request(self, request):
            host = request.get_host().split(':')[0]
            
            # Check if host is an IP address (e.g., 127.0.0.1) or localhost
            if host.replace('.', '').isnumeric() or host == 'localhost':
                subdomain = None
            else:
                host_parts = host.split('.')
                if len(host_parts) < 2:
                    subdomain = None
                else:
                    subdomain = host_parts[0]

            # Ignore 'www'
            if subdomain == 'www':
                subdomain = None

            tenant = None
            if subdomain:
                try:
                    tenant = Tenant.objects.get(subdomain=subdomain)
                except Tenant.DoesNotExist:
                    raise Http404(f"Tenant with subdomain '{subdomain}' not found")
                
                if not tenant.is_active:
                    return HttpResponseForbidden("Tenant inactive.")

            # Set Context
            request.tenant = tenant
            request._tenant_context_token = set_current_tenant(tenant)
            
            return None

    def process_response(self, request, response):
        token = getattr(request, '_tenant_context_token', None)
        if token:
            reset_current_tenant(token)
        return response
    