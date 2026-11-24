from datetime import timedelta
import logging
import secrets
import time
import urllib.parse
from typing import Any
import requests
from django.conf import settings
from django.core import signing
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.core.tenants.models import Tenant
from apps.ghl.models import TenantGHLConnection

logger = logging.getLogger(__name__)

DEFAULT_GHL_AUTH_URL = "https://marketplace.gohighlevel.com/oauth/chooselocation"
DEFAULT_GHL_TOKEN_URL = "https://services.leadconnectorhq.com/oauth/token"
DEFAULT_GHL_SCOPES = [
    "contacts.readonly",
    "contacts.write",
    "locations.readonly",
]


def generate_oauth_state(tenant: Tenant) -> str:
    """
    Generates a cryptographically signed state token embedding tenant ID and nonce.
    Prevents CSRF and links the OAuth flow strictly to the target tenant.
    """
    payload = {
        "tenant_id": str(tenant.id),
        "nonce": secrets.token_hex(16),
        "ts": time.time(),
    }
    return signing.dumps(payload, salt="ghl-oauth-state")


def validate_oauth_state(state: str, max_age_seconds: int = 600) -> Tenant:
    """
    Validates the OAuth state token and returns the bound Tenant.
    Raises ValidationError if invalid, tampered with, or expired.
    """
    if not state:
        raise ValidationError({"state": "State parameter is required."})

    try:
        data = signing.loads(state, salt="ghl-oauth-state", max_age=max_age_seconds)
    except signing.SignatureExpired:
        raise ValidationError({"state": "OAuth state token has expired. Please initiate connection again."})
    except signing.BadSignature:
        raise ValidationError({"state": "Invalid or tampered OAuth state token."})

    tenant_id = data.get("tenant_id")
    if not tenant_id:
        raise ValidationError({"state": "State token missing tenant binding."})

    tenant = Tenant.objects.filter(id=tenant_id, is_active=True).first()
    if not tenant:
        raise ValidationError({"state": f"Tenant {tenant_id} not found or inactive."})

    return tenant


def get_authorization_url(tenant: Tenant, scopes: list[str] | None = None) -> str:
    """
    Builds the GoHighLevel OAuth 2.0 authorization URL for private app location installation.
    """
    client_id = getattr(settings, 'GHL_CLIENT_ID', '')
    redirect_uri = getattr(settings, 'GHL_REDIRECT_URI', '')
    auth_base_url = getattr(settings, 'GHL_AUTH_BASE_URL', None) or DEFAULT_GHL_AUTH_URL

    if not client_id or not redirect_uri:
        logger.warning("GHL_CLIENT_ID or GHL_REDIRECT_URI is not configured in settings.")

    target_scopes = scopes or DEFAULT_GHL_SCOPES
    state = generate_oauth_state(tenant)

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(target_scopes),
        "state": state,
    }

    query_str = urllib.parse.urlencode(params)
    return f"{auth_base_url}?{query_str}"


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    """
    Exchanges authorization code for GHL OAuth access and refresh tokens.
    Endpoint: POST https://services.leadconnectorhq.com/oauth/token
    """
    token_url = getattr(settings, 'GHL_TOKEN_URL', None) or DEFAULT_GHL_TOKEN_URL
    client_id = getattr(settings, 'GHL_CLIENT_ID', '')
    client_secret = getattr(settings, 'GHL_CLIENT_SECRET', '')
    redirect_uri = getattr(settings, 'GHL_REDIRECT_URI', '')

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "user_type": "Location",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    response = requests.post(token_url, data=data, headers=headers, timeout=(5, 15))
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}

    if response.status_code not in (200, 201):
        err_msg = f"Failed to exchange GHL authorization code (status {response.status_code}): {body}"
        logger.error(err_msg)
        raise ValidationError({"ghl_oauth": err_msg})

    return body


def refresh_connection_tokens(connection: TenantGHLConnection) -> TenantGHLConnection:
    """
    Refreshes access token using the stored encrypted refresh token.
    Updates the connection with the new access and refresh tokens and expiration time.
    """
    refresh_token = connection.refresh_token
    if not refresh_token:
        connection.status = TenantGHLConnection.Status.EXPIRED
        connection.save(update_fields=['status'])
        raise ValidationError("Missing refresh token for GHL connection.")

    token_url = getattr(settings, 'GHL_TOKEN_URL', None) or DEFAULT_GHL_TOKEN_URL
    client_id = getattr(settings, 'GHL_CLIENT_ID', '')
    client_secret = getattr(settings, 'GHL_CLIENT_SECRET', '')

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "user_type": connection.user_type or "Location",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    response = requests.post(token_url, data=data, headers=headers, timeout=(5, 15))
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}

    if response.status_code not in (200, 201):
        err_msg = f"Failed to refresh GHL token (status {response.status_code}): {body}"
        logger.error(err_msg)
        connection.status = TenantGHLConnection.Status.EXPIRED
        connection.save(update_fields=['status'])
        raise ValidationError({"ghl_token_refresh": err_msg})

    new_access_token = body.get("access_token")
    new_refresh_token = body.get("refresh_token") or refresh_token
    expires_in = body.get("expires_in", 86400)

    connection.access_token = new_access_token
    connection.refresh_token = new_refresh_token
    connection.token_expires_at = timezone.now() + timedelta(seconds=expires_in)
    connection.status = TenantGHLConnection.Status.CONNECTED
    connection.save(update_fields=[
        'encrypted_access_token',
        'encrypted_refresh_token',
        'token_expires_at',
        'status',
    ])
    logger.info("Successfully refreshed GHL tokens for tenant %s (location %s)", connection.tenant_id, connection.location_id)
    return connection
