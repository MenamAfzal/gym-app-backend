import base64
import hashlib
import logging
import uuid
try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

from django.conf import settings
from django.db import models
from django.utils import timezone
from core_models.base_models import TenantAwareModel

logger = logging.getLogger(__name__)


def _get_ghl_fernet():
    """
    Derives a deterministic 32-byte Fernet key from Django's SECRET_KEY.
    Provides symmetric AES-128-CBC encryption for OAuth access and refresh tokens at rest.
    """
    if Fernet is None:
        logger.warning("cryptography package is not installed; GHL tokens will not be encrypted at rest.")
        return None
    raw_key = getattr(settings, "SECRET_KEY", "fallback-secret-key-32-chars-min")
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_ghl_token(raw: str) -> str:
    """Encrypts raw GHL OAuth token string with Fernet."""
    if not raw or raw.startswith("enc::"):
        return raw or ""
    try:
        f = _get_ghl_fernet()
        if f is None:
            return raw
        token = f.encrypt(raw.encode("utf-8")).decode("utf-8")
        return f"enc::{token}"
    except Exception as e:
        logger.error("Failed to encrypt GHL token: %s", e)
        return raw


def decrypt_ghl_token(ciphertext: str) -> str:
    """Decrypts GHL OAuth token string with Fernet (with plain text fallback)."""
    if not ciphertext or not ciphertext.startswith("enc::"):
        return ciphertext or ""
    try:
        f = _get_ghl_fernet()
        if f is None:
            return ciphertext
        token = ciphertext[5:]
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error("Failed to decrypt GHL token: %s", e)
        return ciphertext


class TenantGHLConnection(TenantAwareModel):
    """
    Tenant-specific GoHighLevel (GHL) OAuth connection.
    Each Fitverx tenant has its own isolated connection with its own GHL location ID and credentials.
    Access and refresh tokens are encrypted at rest using AES-128-CBC / HMAC-SHA256 (Fernet).
    """
    class Status(models.TextChoices):
        CONNECTED = 'connected', 'Connected'
        DISCONNECTED = 'disconnected', 'Disconnected'
        EXPIRED = 'expired', 'Expired'
        ERROR = 'error', 'Error'

    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='ghl_connection',
        help_text='Fitverx tenant associated with this GHL connection'
    )
    location_id = models.CharField(
        max_length=128,
        blank=True,
        db_index=True,
        help_text='GoHighLevel Location ID for this tenant'
    )
    encrypted_access_token = models.TextField(
        blank=True,
        default='',
        help_text='Encrypted GHL OAuth access token'
    )
    encrypted_refresh_token = models.TextField(
        blank=True,
        default='',
        help_text='Encrypted GHL OAuth refresh token'
    )
    token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when the current access token expires'
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.DISCONNECTED,
        db_index=True,
        help_text='Current GHL connection status'
    )
    connected_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when the connection was authorized'
    )
    company_id = models.CharField(
        max_length=128,
        blank=True,
        default='',
        help_text='GHL Agency / Company ID'
    )
    user_id = models.CharField(
        max_length=128,
        blank=True,
        default='',
        help_text='GHL user ID who authorized the integration'
    )
    user_type = models.CharField(
        max_length=64,
        blank=True,
        default='Location',
        help_text='GHL userType (e.g. Location or Company)'
    )
    scope = models.CharField(
        max_length=512,
        blank=True,
        default='',
        help_text='OAuth scopes granted during authorization'
    )

    class Meta:
        verbose_name = 'Tenant GoHighLevel Connection'
        verbose_name_plural = 'Tenant GoHighLevel Connections'

    def __str__(self):
        tenant_name = self.tenant.name if self.tenant else 'Unknown'
        return f"GHL Connection ({tenant_name}) - Location: {self.location_id or 'Unset'} [{self.status}]"

    def get_access_token(self) -> str:
        """Returns the decrypted raw access token."""
        return decrypt_ghl_token(self.encrypted_access_token)

    def set_access_token(self, raw_token: str) -> None:
        """Encrypts and sets the access token."""
        self.encrypted_access_token = encrypt_ghl_token(raw_token)

    @property
    def access_token(self) -> str:
        return self.get_access_token()

    @access_token.setter
    def access_token(self, value: str):
        self.set_access_token(value)

    def get_refresh_token(self) -> str:
        """Returns the decrypted raw refresh token."""
        return decrypt_ghl_token(self.encrypted_refresh_token)

    def set_refresh_token(self, raw_token: str) -> None:
        """Encrypts and sets the refresh token."""
        self.encrypted_refresh_token = encrypt_ghl_token(raw_token)

    @property
    def refresh_token(self) -> str:
        return self.get_refresh_token()

    @refresh_token.setter
    def refresh_token(self, value: str):
        self.set_refresh_token(value)

    @property
    def is_connected(self) -> bool:
        """Checks if connection is active with valid credentials."""
        return (
            self.status == self.Status.CONNECTED
            and bool(self.location_id)
            and bool(self.encrypted_access_token)
        )

    @property
    def is_token_expired(self) -> bool:
        """Checks if the access token has expired or is expiring in <= 60 seconds."""
        if not self.token_expires_at:
            return True
        return timezone.now() >= (self.token_expires_at - timezone.timedelta(seconds=60))

    def save(self, *args, **kwargs):
        if self.encrypted_access_token and not self.encrypted_access_token.startswith("enc::"):
            self.encrypted_access_token = encrypt_ghl_token(self.encrypted_access_token)
        if self.encrypted_refresh_token and not self.encrypted_refresh_token.startswith("enc::"):
            self.encrypted_refresh_token = encrypt_ghl_token(self.encrypted_refresh_token)
        super().save(*args, **kwargs)


class GHLOutboxEvent(TenantAwareModel):
    """
    Transactional Outbox record representing an outgoing purchase event for GoHighLevel synchronization.
    Guarantees reliable, retryable, tenant-isolated, and idempotent delivery.
    """
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        DELIVERED = 'delivered', 'Delivered'
        FAILED = 'failed', 'Failed'
        ABANDONED = 'abandoned', 'Abandoned'

    event_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text='Globally unique event identifier used for idempotency'
    )
    payment_ref = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        help_text='Payment reference or checkout session ID preventing duplicate purchase processing'
    )
    event_type = models.CharField(
        max_length=100,
        default='package.purchased',
        db_index=True,
        help_text='Event type identifier'
    )
    occurred_at = models.DateTimeField(
        default=timezone.now,
        help_text='Timestamp of the purchase'
    )
    payload = models.JSONField(
        default=dict,
        help_text='Purchase details: customer name, email, phone, package name, amount paid'
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True
    )
    attempts = models.PositiveIntegerField(
        default=0,
        help_text='Number of synchronization attempts made'
    )
    last_attempt_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp of the most recent delivery attempt'
    )
    response_status_code = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='HTTP status code returned by GHL API'
    )
    response_body = models.JSONField(
        default=dict,
        blank=True,
        help_text='Response body received from GHL API'
    )
    error_message = models.TextField(
        blank=True,
        default='',
        help_text='Error details if the delivery attempt failed'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'GHL Outbox Event'
        verbose_name_plural = 'GHL Outbox Events'
        indexes = [
            models.Index(fields=['tenant', 'status', 'created_at']),
            models.Index(fields=['tenant', 'payment_ref']),
        ]

    def __str__(self):
        tenant_name = self.tenant.name if self.tenant else 'Unknown'
        return f"GHL Event {self.event_id} ({tenant_name}) - {self.status}"
