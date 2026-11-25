import hashlib
import hmac
import json
import logging
import time
from typing import Any
import requests

logger = logging.getLogger(__name__)


class CRMClientError(Exception):
    def __init__(self, message: str, status_code: int | None = None, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class CRMClient:
    """
    HTTP client for the CRM External API.
    Handles exact HMAC-SHA256 request signing and header construction
    according to the CRM's APIKeyHMACAuthentication specification.
    """
    def __init__(
        self,
        base_url: str,
        api_key_id: str,
        api_secret: str,
        timeout: int = 10,
        session: requests.Session | None = None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key_id = (api_key_id or "").strip()
        self.api_secret = (api_secret or "").strip()
        self.timeout = timeout
        self.session = session or requests.Session()

    def _build_signature(self, timestamp: str, body_bytes: bytes) -> str:
        """
        Computes HMAC-SHA256 hex digest for canonical string: f"{timestamp}.{raw_body}".
        """
        canonical = f"{timestamp}.".encode("utf-8") + (body_bytes or b"")
        secret_bytes = self.api_secret.encode("utf-8")
        return hmac.new(secret_bytes, canonical, hashlib.sha256).hexdigest()

    def _build_headers(self, body_bytes: bytes = b"") -> dict[str, str]:
        """
        Constructs required CRM authentication headers.
        CRITICAL: Never include X-Tenant or tenant_id; doing so triggers 403 Forbidden.
        """
        timestamp = str(int(time.time()))
        signature = self._build_signature(timestamp, body_bytes)
        return {
            "Content-Type": "application/json",
            "X-API-Key": self.api_key_id,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self.base_url}{path}"
        body_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8") if data is not None else b""
        headers = self._build_headers(body_bytes)

        try:
            resp = self.session.request(
                method=method,
                url=url,
                data=body_bytes if data is not None else None,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.error("CRM request failed for %s %s: %s", method, url, str(exc))
            raise CRMClientError(f"Connection error reaching CRM: {exc}") from exc

        try:
            resp_json = resp.json()
        except ValueError:
            resp_json = {"raw": resp.text}

        return resp.status_code, resp_json

    def sync_bulk_packages(
        self,
        packages: list[dict[str, Any]],
        branding: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """
        Bulk synchronize packages (and optional tenant branding) via POST /api/v1/external/packages/sync/
        Accepted response: 200 OK.
        """
        payload: dict[str, Any] = {"packages": packages}
        if branding:
            payload["branding"] = branding
            if branding.get("logo_url") is not None:
                payload["logo_url"] = branding.get("logo_url")
        return self._request(
            method="POST",
            path="/api/v1/external/packages/sync/",
            data=payload,
        )

    def sync_branding(self, branding_data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """
        Synchronize tenant branding colors and logo via POST /api/v1/external/branding/
        Accepted response: 200 OK or 201 Created.
        """
        return self._request(
            method="POST",
            path="/api/v1/external/branding/",
            data=branding_data,
        )

    def sync_package(self, package_data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """
        Upsert a single package via POST /api/v1/external/packages/
        Accepted response: 200 OK or 201 Created.
        """
        return self._request(
            method="POST",
            path="/api/v1/external/packages/",
            data=package_data,
        )

    def get_package(self, external_id_or_id: str) -> tuple[int, dict[str, Any]]:
        """
        Retrieve package details via GET /api/v1/external/packages/<external_id_or_id>/
        Accepted response: 200 OK.
        """
        return self._request(
            method="GET",
            path=f"/api/v1/external/packages/{external_id_or_id}/",
        )

    def ingest_event(self, event_envelope: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """
        Ingest signed business event via POST /api/v1/external/events/
        Accepted responses:
        - 202 Accepted: event accepted for processing
        - 200 OK: idempotent duplicate event
        """
        return self._request(
            method="POST",
            path="/api/v1/external/events/",
            data=event_envelope,
        )

    def upsert_contact(self, contact_data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """
        Direct contact upsert via POST /api/v1/external/contacts/upsert/
        Accepted responses: 200 OK or 201 Created.
        """
        return self._request(
            method="POST",
            path="/api/v1/external/contacts/upsert/",
            data=contact_data,
        )

    def get_contact(self, external_id_or_id: str) -> tuple[int, dict[str, Any]]:
        """
        Retrieve contact details via GET /api/v1/external/contacts/<external_id_or_id>/
        """
        return self._request(
            method="GET",
            path=f"/api/v1/external/contacts/{external_id_or_id}/",
        )

    def create_appointment(self, appointment_data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """
        Create appointment via POST /api/v1/external/appointments/
        """
        return self._request(
            method="POST",
            path="/api/v1/external/appointments/",
            data=appointment_data,
        )

    def update_appointment(self, appointment_id: str, data: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """
        Update appointment via PATCH /api/v1/external/appointments/<appointment_id>/
        """
        return self._request(
            method="PATCH",
            path=f"/api/v1/external/appointments/{appointment_id}/",
            data=data,
        )
