import logging
import re
from typing import Any
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"


class GHLApiError(Exception):
    """Base exception for GoHighLevel API interactions."""
    def __init__(self, message: str, status_code: int | None = None, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class GHLAuthError(GHLApiError):
    """Raised when GHL rejects credentials (401 Unauthorized), triggering token refresh."""
    pass


class GHLRateLimitError(GHLApiError):
    """Raised when GHL rate limits requests (429 Too Many Requests)."""
    def __init__(self, message: str, retry_after: int = 60, status_code: int = 429, response_body: Any = None):
        super().__init__(message, status_code, response_body)
        self.retry_after = retry_after


class GHLNotFoundError(GHLApiError):
    """Raised when a GHL resource is not found (404)."""
    pass


def normalize_phone(phone: str) -> str:
    """Strips non-digit characters except leading plus for standard comparison."""
    if not phone:
        return ""
    clean = phone.strip()
    has_plus = clean.startswith('+')
    digits = re.sub(r'\D', '', clean)
    return f"+{digits}" if has_plus else digits


class GHLClient:
    """
    HTTP client for GoHighLevel (LeadConnector) API v2.
    Scoped to a specific GHL Location and authorized via OAuth bearer token.
    """

    def __init__(
        self,
        access_token: str,
        location_id: str,
        base_url: str | None = None,
        timeout: tuple[int, int] = (5, 15),
    ):
        self.access_token = access_token
        self.location_id = location_id
        self.base_url = (base_url or getattr(settings, 'GHL_API_BASE_URL', None) or DEFAULT_GHL_API_BASE).rstrip('/')
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Version": GHL_API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = self._headers()

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=json_data,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout as exc:
            logger.error("GHL request timeout (%s %s): %s", method, url, exc)
            raise GHLApiError(f"GHL API request timed out: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            logger.error("GHL request connection failure (%s %s): %s", method, url, exc)
            raise GHLApiError(f"GHL API connection failure: {exc}") from exc

        status_code = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text}

        if status_code in (200, 201):
            return body

        if status_code == 401:
            logger.warning("GHL returned 401 Unauthorized: %s", body)
            raise GHLAuthError("GHL authentication failed or token expired", status_code=401, response_body=body)

        if status_code == 404:
            raise GHLNotFoundError(f"GHL resource not found: {endpoint}", status_code=404, response_body=body)

        if status_code == 429:
            retry_after_header = response.headers.get("Retry-After")
            try:
                retry_after = int(retry_after_header) if retry_after_header else 60
            except ValueError:
                retry_after = 60
            logger.warning("GHL rate limit exceeded (retry after %ss): %s", retry_after, body)
            raise GHLRateLimitError("GHL rate limit exceeded", retry_after=retry_after, status_code=429, response_body=body)

        err_msg = f"GHL API error {status_code} ({method} {endpoint}): {body}"
        logger.error(err_msg)
        raise GHLApiError(err_msg, status_code=status_code, response_body=body)

    def search_contacts(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """
        Searches contacts in the connected GHL location by query (email, phone, or name).
        Endpoint: GET /contacts/?locationId={locationId}&query={query}
        """
        if not query:
            return []
        params = {
            "locationId": self.location_id,
            "query": query.strip(),
            "limit": limit,
        }
        res = self._request("GET", "/contacts/", params=params)
        return res.get("contacts") or []

    def lookup_contact(self, email: str | None = None, phone: str | None = None) -> dict[str, Any] | None:
        """
        Searches for an existing contact in the connected GHL location using email and/or phone.
        Prioritizes exact email match, then falls back to normalized phone match.
        """
        clean_email = email.strip().lower() if email else None
        clean_phone = normalize_phone(phone) if phone else None

        # 1. Search by email
        if clean_email:
            contacts = self.search_contacts(clean_email)
            for c in contacts:
                contact_email = (c.get("email") or "").strip().lower()
                if contact_email == clean_email:
                    return c

        # 2. Search by phone
        if clean_phone:
            contacts = self.search_contacts(phone.strip())
            for c in contacts:
                contact_phone = normalize_phone(c.get("phone") or "")
                if contact_phone and contact_phone == clean_phone:
                    return c

        return None

    def create_contact(self, contact_data: dict[str, Any]) -> dict[str, Any]:
        """
        Creates a new contact in the connected GHL location.
        Endpoint: POST /contacts/
        """
        payload = dict(contact_data)
        payload["locationId"] = self.location_id
        return self._request("POST", "/contacts/", json_data=payload)

    def update_contact(self, contact_id: str, contact_data: dict[str, Any]) -> dict[str, Any]:
        """
        Updates an existing contact in GHL.
        Endpoint: PUT /contacts/{contactId}
        """
        clean_id = str(contact_id).strip()
        payload = dict(contact_data)
        return self._request("PUT", f"/contacts/{clean_id}", json_data=payload)

    def add_contact_note(self, contact_id: str, note_body: str) -> dict[str, Any]:
        """
        Adds a note to a contact card in GHL.
        Endpoint: POST /contacts/{contactId}/notes
        """
        clean_id = str(contact_id).strip()
        payload = {"body": note_body}
        return self._request("POST", f"/contacts/{clean_id}/notes", json_data=payload)

    def upsert_contact(self, contact_data: dict[str, Any]) -> dict[str, Any]:
        """
        Upserts a contact in GHL (searches by email/phone; updates if found, creates if not).
        Endpoint: POST /contacts/upsert
        """
        payload = dict(contact_data)
        payload["locationId"] = self.location_id
        return self._request("POST", "/contacts/upsert", json_data=payload)
