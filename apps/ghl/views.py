from datetime import timedelta
import logging
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from apps.core.tenants.context import get_current_tenant
from apps.ghl.models import TenantGHLConnection
from apps.ghl.oauth import (
    exchange_code_for_tokens,
    get_authorization_url,
    validate_oauth_state,
)

logger = logging.getLogger(__name__)


def _resolve_tenant_for_request(request):
    """Resolves the tenant for an authenticated API request."""
    return get_current_tenant() or getattr(request.user, "tenant", None)


class GHLOAuthConnectView(APIView):
    """
    Initiates GoHighLevel OAuth 2.0 flow for the authenticated tenant.
    Returns the secure authorization URL with an embedded signed state token.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _resolve_tenant_for_request(request)
        if not tenant:
            return Response(
                {"detail": "Tenant context is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        auth_url = get_authorization_url(tenant)
        return Response({
            "auth_url": auth_url,
            "tenant_id": str(tenant.id),
        }, status=status.HTTP_200_OK)


class GHLOAuthCallbackView(APIView):
    """
    OAuth callback endpoint redirected to by GoHighLevel upon user authorization.
    Validates state token, exchanges code for access & refresh tokens, and saves tenant connection.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        error = request.query_params.get("error")

        if error:
            error_description = request.query_params.get("error_description", error)
            logger.warning("GoHighLevel OAuth error callback: %s (%s)", error, error_description)
            return Response({
                "error": error,
                "error_description": error_description
            }, status=status.HTTP_400_BAD_REQUEST)

        if not code:
            return Response(
                {"error": "missing_code", "detail": "Authorization code is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not state:
            return Response(
                {"error": "missing_state", "detail": "OAuth state parameter is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            tenant = validate_oauth_state(state)
        except Exception as exc:
            logger.warning("Invalid OAuth state received: %s", exc)
            return Response(
                {"error": "invalid_state", "detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            token_data = exchange_code_for_tokens(code)
        except Exception as exc:
            logger.error("Token exchange failed for tenant %s: %s", tenant.id, exc)
            return Response(
                {"error": "token_exchange_failed", "detail": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY
            )

        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 86400)
        location_id = token_data.get("locationId", "")
        company_id = token_data.get("companyId", "")
        user_id = token_data.get("userId", "")
        user_type = token_data.get("userType", "Location")
        scope = token_data.get("scope", "")

        connection, _ = TenantGHLConnection.all_objects.get_or_create(tenant=tenant)
        connection.location_id = location_id
        connection.access_token = access_token
        connection.refresh_token = refresh_token
        connection.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
        connection.company_id = company_id
        connection.user_id = user_id
        connection.user_type = user_type
        connection.scope = scope
        connection.status = TenantGHLConnection.Status.CONNECTED
        connection.connected_at = timezone.now()
        connection.save()

        logger.info("Successfully connected GoHighLevel for tenant %s (location %s)", tenant.id, location_id)
        return Response({
            "status": "connected",
            "tenant_id": str(tenant.id),
            "location_id": location_id,
            "connected_at": connection.connected_at.isoformat(),
        }, status=status.HTTP_200_OK)


class GHLConnectionStatusView(APIView):
    """
    Returns the current GoHighLevel connection status for the authenticated tenant.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = _resolve_tenant_for_request(request)
        if not tenant:
            return Response(
                {"detail": "Tenant context is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        connection = TenantGHLConnection.all_objects.filter(tenant=tenant).first()
        is_connected = connection.is_connected if connection else False
        current_status = connection.status if connection else TenantGHLConnection.Status.DISCONNECTED

        return Response({
            "tenant_id": str(tenant.id),
            "connected": is_connected,
            "status": current_status,
            "location_id": connection.location_id if connection else None,
            "connected_at": connection.connected_at.isoformat() if connection and connection.connected_at else None,
            "token_expires_at": connection.token_expires_at.isoformat() if connection and connection.token_expires_at else None,
        }, status=status.HTTP_200_OK)


class GHLDisconnectView(APIView):
    """
    Disconnects the tenant's GoHighLevel integration and clears stored credentials.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tenant = _resolve_tenant_for_request(request)
        if not tenant:
            return Response(
                {"detail": "Tenant context is required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        connection = TenantGHLConnection.all_objects.filter(tenant=tenant).first()
        if connection:
            connection.status = TenantGHLConnection.Status.DISCONNECTED
            connection.encrypted_access_token = ""
            connection.encrypted_refresh_token = ""
            connection.token_expires_at = None
            connection.save()
            logger.info("Disconnected GoHighLevel for tenant %s", tenant.id)

        return Response({
            "status": "disconnected",
            "tenant_id": str(tenant.id),
        }, status=status.HTTP_200_OK)
