from django.utils.deprecation import MiddlewareMixin

class DisableCSRFOnAPI(MiddlewareMixin):
    """
    Strictly disables CSRF checks for any URL starting with /api/
    This is safe because we use JWT (Stateless) for these endpoints.
    """
    def process_request(self, request):
        if request.path.startswith('/api/'):
            setattr(request, '_dont_enforce_csrf_checks', True)
            