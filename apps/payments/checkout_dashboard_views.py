from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.payments.checkout_dashboard_serializers import TenantCheckoutConfigAdminSerializer
from apps.payments.models import TenantCheckoutConfig
from apps.users.models import UserRole


class IsTenantAdminOrOwner(BasePermission):
    def has_permission(self, request, view):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return getattr(user, 'role', None) in [
            UserRole.GYM_OWNER,
            UserRole.GYM_MANAGER,
            UserRole.PLATFORM_ADMIN,
        ]


def _resolve_tenant_for_admin(request):
    user = request.user
    if user.is_superuser or user.role == UserRole.PLATFORM_ADMIN:
        tenant_id = request.query_params.get('tenant_id') or request.headers.get('X-Tenant-Id')
        if tenant_id:
            from apps.core.tenants.models import Tenant
            return Tenant.objects.filter(id=tenant_id).first()
    return getattr(user, 'tenant', None)


class TenantCheckoutConfigAdminView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdminOrOwner]

    def get(self, request):
        tenant = _resolve_tenant_for_admin(request)
        if not tenant:
            return Response({'detail': 'Tenant context not found.'}, status=status.HTTP_404_NOT_FOUND)

        config, _ = TenantCheckoutConfig.objects.get_or_create(tenant=tenant)
        serializer = TenantCheckoutConfigAdminSerializer(config, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request):
        return self._update(request, partial=False)

    def patch(self, request):
        return self._update(request, partial=True)

    def _update(self, request, partial=False):
        tenant = _resolve_tenant_for_admin(request)
        if not tenant:
            return Response({'detail': 'Tenant context not found.'}, status=status.HTTP_404_NOT_FOUND)

        config, _ = TenantCheckoutConfig.objects.get_or_create(tenant=tenant)
        serializer = TenantCheckoutConfigAdminSerializer(
            config,
            data=request.data,
            partial=partial,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class WidgetPreviewView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdminOrOwner]

    def get(self, request):
        tenant = _resolve_tenant_for_admin(request)
        if not tenant:
            return Response({'detail': 'Tenant context not found.'}, status=status.HTTP_404_NOT_FOUND)

        base_url = request.build_absolute_uri('/')[:-1]
        html_preview = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Widget Live Preview - {tenant.name}</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #f9fafb;
      margin: 0;
      padding: 24px;
    }}
    .preview-banner {{
      background: #111827;
      color: #ffffff;
      padding: 12px 16px;
      border-radius: 8px;
      margin-bottom: 24px;
      font-size: 13px;
    }}
  </style>
</head>
<body>
  <div class="preview-banner">
    Interactive Live Preview for <strong>{tenant.name}</strong>. This preview reflects your current website integration settings.
  </div>
  <div id="fitverx-checkout-widget" data-tenant-id="{tenant.id}"></div>
  <script src="{base_url}/static/checkout/fitverx-widget.js" async></script>
</body>
</html>"""
        return HttpResponse(html_preview, content_type='text/html')


class WidgetEmbedCodeView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdminOrOwner]

    def get(self, request):
        tenant = _resolve_tenant_for_admin(request)
        if not tenant:
            return Response({'detail': 'Tenant context not found.'}, status=status.HTTP_404_NOT_FOUND)

        base_url = request.build_absolute_uri('/')[:-1]
        snippet = (
            f'<div id="fitverx-checkout-widget" data-tenant-id="{tenant.id}"></div>\n'
            f'<script src="{base_url}/static/checkout/fitverx-widget.js" async></script>'
        )

        return Response({
            'tenant_id': str(tenant.id),
            'html_snippet': snippet,
            'guides': {
                'html': 'Paste the snippet directly into the HTML body where you want your membership plans to appear.',
                'wordpress': 'Add a Custom HTML block in the Gutenberg editor or Elementor HTML widget and paste the snippet.',
                'webflow': 'Add an Embed element on your page and paste the code snippet.',
                'squarespace': 'Add a Code block, set display mode to HTML, and paste the code snippet.',
                'wix': 'Add an HTML iframe or Custom Element and embed the script and container.',
            }
        }, status=status.HTTP_200_OK)


class TenantWebsiteIntegrationDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsTenantAdminOrOwner]

    def get(self, request):
        tenant = _resolve_tenant_for_admin(request)
        if not tenant:
            return Response({'detail': 'Tenant context not found.'}, status=status.HTTP_404_NOT_FOUND)

        config, _ = TenantCheckoutConfig.objects.get_or_create(tenant=tenant)
        base_url = request.build_absolute_uri('/')[:-1]
        snippet = (
            f'<div id="fitverx-checkout-widget" data-tenant-id="{tenant.id}"></div>\n'
            f'<script src="{base_url}/static/checkout/fitverx-widget.js" async></script>'
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{tenant.name} - Website Checkout Integration</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: #f4f6f8;
      color: #111827;
      padding: 32px 20px;
    }}
    .dashboard-container {{
      max-width: 1000px;
      margin: 0 auto;
    }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
    }}
    .card {{
      background: #ffffff;
      border-radius: 12px;
      padding: 24px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.08);
      margin-bottom: 24px;
    }}
    h1 {{ font-size: 24px; font-weight: 700; }}
    h2 {{ font-size: 18px; font-weight: 600; margin-bottom: 16px; }}
    p {{ color: #4b5563; font-size: 14px; line-height: 1.5; margin-bottom: 16px; }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 9999px;
      font-size: 12px;
      font-weight: 600;
    }}
    .badge-enabled {{ background: #def7ec; color: #03543f; }}
    .badge-disabled {{ background: #fde8e8; color: #9b1c1c; }}
    pre {{
      background: #1f2937;
      color: #f9fafb;
      padding: 16px;
      border-radius: 8px;
      font-family: monospace;
      font-size: 13px;
      overflow-x: auto;
      margin-bottom: 16px;
    }}
    .btn {{
      display: inline-block;
      background: #2563eb;
      color: #ffffff;
      padding: 10px 18px;
      border-radius: 6px;
      font-weight: 600;
      font-size: 14px;
      border: none;
      cursor: pointer;
      text-decoration: none;
    }}
    .preview-frame {{
      width: 100%;
      height: 480px;
      border: 1px solid #e5e7eb;
      border-radius: 8px;
    }}
  </style>
</head>
<body>
  <div class="dashboard-container">
    <div class="header">
      <div>
        <h1>Website Checkout Integration</h1>
        <p>Embed membership packages directly into your external website.</p>
      </div>
      <div>
        <span class="badge {'badge-enabled' if config.is_enabled else 'badge-disabled'}">
          {'Checkout Enabled' if config.is_enabled else 'Checkout Disabled'}
        </span>
      </div>
    </div>

    <div class="card">
      <h2>Embed Code</h2>
      <p>Copy and paste this HTML snippet into your website where you want the membership checkout widget to appear:</p>
      <pre><code>{snippet}</code></pre>
    </div>

    <div class="card">
      <h2>Live Preview</h2>
      <iframe src="/api/v1/payments/checkout-config/preview/" class="preview-frame"></iframe>
    </div>
  </div>
</body>
</html>"""
        return HttpResponse(html, content_type='text/html')

