"""
Tenant Middleware (Hybrid)

Resolves tenant from:
1. Subdomain (Primary - for public pages/isolation)
2. JWT Token (Fallback - for API/Postman/Mobile execution)
"""
import jwt
from django.conf import settings
from django.http import Http404, HttpResponseForbidden
from django.utils.deprecation import MiddlewareMixin
from apps.core.tenants.models import Tenant
from apps.core.tenants.context import set_current_tenant, reset_current_tenant

class TenantMiddleware(MiddlewareMixin):
    
    def process_request(self, request):
        tenant = None
        
        # ------------------------------------------------------------------
        # STRATEGY 1: Subdomain Resolution (Preferred for Web/Public)
        # ------------------------------------------------------------------
        host = request.get_host().split(':')[0]
        subdomain = None
        
        if not (host.replace('.', '').isnumeric() or host == 'localhost'):
            host_parts = host.split('.')
            if len(host_parts) >= 2 and host_parts[0] != 'www':
                subdomain = host_parts[0]

        if subdomain:
            try:
                tenant = Tenant.objects.get(subdomain=subdomain)
            except Tenant.DoesNotExist:
                raise Http404(f"Tenant '{subdomain}' not found")

        # ------------------------------------------------------------------
        # STRATEGY 2: JWT Resolution (Fallback for API/IP/Postman)
        # ------------------------------------------------------------------
        if not tenant:
            auth_header = request.headers.get('Authorization')
            if auth_header and auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
                try:
                    # We decode purely to read the claim. 
                    # DRF will verify signature/expiry later in the view.
                    # Ideally, use settings.SECRET_KEY to verify signature here too for safety.
                    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
                    tenant_id = payload.get('tenant_id')
                    
                    if tenant_id:
                        try:
                            tenant = Tenant.objects.get(id=tenant_id)
                        except Tenant.DoesNotExist:
                            pass # Token refers to deleted tenant? Ignore.
                            
                except jwt.ExpiredSignatureError:
                    pass # Let DRF handle auth errors
                except jwt.DecodeError:
                    pass # Invalid token
        
        # ------------------------------------------------------------------
        # Final Validation
        # ------------------------------------------------------------------
        if tenant and not tenant.is_active:
            return HttpResponseForbidden("Tenant is inactive.")

        # Set Context
        request.tenant = tenant
        request._tenant_context_token = set_current_tenant(tenant)
        
        return None

    def process_response(self, request, response):
        token = getattr(request, '_tenant_context_token', None)
        if token:
            reset_current_tenant(token)
        return response
    