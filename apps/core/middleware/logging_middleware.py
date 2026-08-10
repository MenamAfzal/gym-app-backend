import json
import logging
import time
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger('apps.request_logger')

# Sensitive keys to redact from logs
SENSITIVE_KEYS = {
    'password', 'token', 'access', 'refresh', 'secret', 'key', 'card', 
    'otp', 'code', 'pin', 'cvc', 'authorization', 'cookie', 'stripe_secret'
}

def sanitize_data(data):
    """
    Recursively redacts sensitive keys from dictionaries and lists.
    """
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if any(sensitive in k.lower() for sensitive in SENSITIVE_KEYS):
                sanitized[k] = '[REDACTED]'
            else:
                sanitized[k] = sanitize_data(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_data(item) for item in data]
    return data

class RequestResponseLoggingMiddleware(MiddlewareMixin):
    """
    Middleware to log all API requests and responses.
    Sanitizes sensitive information to protect credentials and private user details.
    """
    def process_request(self, request):
        # Only log API paths
        if not request.path.startswith('/api/'):
            return None
            
        request._start_time = time.time()
        
        method = request.method
        path = request.path
        
        # Get and sanitize query parameters
        query_params = dict(request.GET.items())
        sanitized_query = sanitize_data(query_params)
        
        body_log = ""
        # Safely read and sanitize JSON bodies without consuming stream or failing on files
        if request.content_type == 'application/json':
            if request.body:
                try:
                    body_data = json.loads(request.body)
                    sanitized_body = sanitize_data(body_data)
                    body_log = f" | Body: {json.dumps(sanitized_body)}"
                except Exception:
                    pass
        elif request.content_type in ['application/x-www-form-urlencoded', 'multipart/form-data']:
            try:
                post_data = dict(request.POST.items())
                sanitized_body = sanitize_data(post_data)
                body_log = f" | Body (Form): {json.dumps(sanitized_body)}"
            except Exception:
                pass
                
        user_email = request.user.email if hasattr(request, 'user') and request.user.is_authenticated else 'Anonymous'
        tenant_name = request.tenant.name if hasattr(request, 'tenant') and request.tenant else 'None'
        
        logger.info(
            f"API Request: {method} {path} | User: {user_email} | Tenant: {tenant_name} | Query: {sanitized_query}{body_log}"
        )
        return None

    def process_response(self, request, response):
        if not request.path.startswith('/api/'):
            return response
            
        duration = ""
        if hasattr(request, '_start_time'):
            duration = f" | Duration: {time.time() - request._start_time:.3f}s"
            
        status_code = response.status_code
        content_type = response.get('Content-Type', '')
        
        body_log = ""
        # Safely parse and sanitize JSON response bodies
        if 'application/json' in content_type and response.content:
            try:
                body_data = json.loads(response.content)
                sanitized_body = sanitize_data(body_data)
                body_log = f" | Response: {json.dumps(sanitized_body)}"
            except Exception:
                pass
                
        user_email = request.user.email if hasattr(request, 'user') and request.user.is_authenticated else 'Anonymous'
        tenant_name = request.tenant.name if hasattr(request, 'tenant') and request.tenant else 'None'

        logger.info(
            f"API Response: {request.method} {request.path} | Status: {status_code}{duration} | User: {user_email} | Tenant: {tenant_name}{body_log}"
        )
        return response
