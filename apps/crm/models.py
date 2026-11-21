import base64
import hashlib
import uuid
from cryptography.fernet import Fernet
from django.conf import settings
from django.db import models
from django.utils import timezone
from core_models.base_models import TenantAwareModel


def _get_crm_fernet() -> Fernet:
    """Derives a deterministic 32-byte Fernet key from Django's SECRET_KEY."""
    raw_key = getattr(settings, "SECRET_KEY", "fallback-secret-key-32-chars-min")
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_crm_secret(raw: str) -> str:
    """Encrypts raw CRM API secret string with Fernet."""
    if not raw or raw.startswith("enc::"):
        return raw or ""
    try:
        f = _get_crm_fernet()
        token = f.encrypt(raw.encode("utf-8")).decode("utf-8")
        return f"enc::{token}"
    except Exception:
        return raw


def decrypt_crm_secret(ciphertext: str) -> str:
    """Decrypts CRM API secret string with Fernet (with plain text fallback)."""
    if not ciphertext or not ciphertext.startswith("enc::"):
        return ciphertext or ""
    try:
        f = _get_crm_fernet()
        token = ciphertext[5:]
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except Exception:
        return ciphertext


class TenantCRMConfig(TenantAwareModel):
    """
    Tenant-specific configuration for integrating with the external CRM service.
    Holds the tenant's HMAC credentials and automation directives.
    API secrets are encrypted at rest using AES-128-CBC / HMAC-SHA256 (Fernet).
    """
    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='crm_config',
        help_text='Tenant associated with these CRM credentials'
    )
    api_key_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        help_text='CRM Inbound Tenant API Key ID (sent as X-API-Key)'
    )
    api_secret = models.CharField(
        max_length=512,
        blank=True,
        default='',
        help_text='Encrypted CRM Inbound Tenant API Key Secret used for HMAC-SHA256 signature computation'
    )
    crm_base_url = models.URLField(
        max_length=255,
        default='http://localhost:8000',
        help_text='Base URL of the target CRM instance'
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Whether the CRM integration is actively enabled for this tenant'
    )
    sync_packages_on_save = models.BooleanField(
        default=True,
        help_text='Automatically sync package types to CRM when created or updated'
    )
    stop_lead_workflow_name = models.CharField(
        max_length=255,
        default='Lead Follow-Up Sequence',
        blank=True,
        help_text='Name of promotional workflow in CRM to stop when a package is purchased'
    )
    start_onboarding_workflow_name = models.CharField(
        max_length=255,
        default='Post-Purchase Onboarding',
        blank=True,
        help_text='Name of onboarding workflow in CRM to trigger when a package is purchased'
    )

    class Meta:
        verbose_name = 'Tenant CRM Configuration'
        verbose_name_plural = 'Tenant CRM Configurations'

    def __str__(self):
        return f"CRM Config ({self.tenant.name}) - Active: {self.is_active}"

    def get_api_secret(self) -> str:
        """Returns the decrypted raw API secret for HMAC computation."""
        return decrypt_crm_secret(self.api_secret)

    @property
    def raw_api_secret(self) -> str:
        """Property returning the decrypted raw API secret."""
        return self.get_api_secret()

    def set_api_secret(self, raw_secret: str) -> None:
        """Encrypts and sets the API secret."""
        self.api_secret = encrypt_crm_secret(raw_secret)

    def save(self, *args, **kwargs):
        if self.api_secret and not self.api_secret.startswith("enc::"):
            self.api_secret = encrypt_crm_secret(self.api_secret)
        super().save(*args, **kwargs)

    @property
    def is_configured(self) -> bool:
        return bool(self.is_active and self.api_key_id and self.api_secret and self.crm_base_url)


class CRMOutboxEvent(TenantAwareModel):
    """
    Transactional Outbox record representing an outgoing event for CRM ingestion.
    Guarantees reliable, retryable, tenant-scoped and idempotent delivery.
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
        help_text='Globally unique event identifier used for CRM idempotency'
    )
    event_type = models.CharField(
        max_length=100,
        db_index=True,
        help_text='CRM event type (e.g. package.purchased, subscription.renewed)'
    )
    occurred_at = models.DateTimeField(
        default=timezone.now,
        help_text='Event occurrence timestamp'
    )
    payload = models.JSONField(
        default=dict,
        help_text='Exact event envelope dispatched to CRM'
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True
    )
    attempts = models.PositiveIntegerField(
        default=0,
        help_text='Number of delivery attempts made'
    )
    last_attempt_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp of the most recent delivery attempt'
    )
    response_status_code = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='HTTP status code returned by CRM'
    )
    response_body = models.JSONField(
        default=dict,
        blank=True,
        help_text='Response body received from CRM'
    )
    error_message = models.TextField(
        blank=True,
        default='',
        help_text='Error details if the delivery attempt failed'
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'CRM Outbox Event'
        verbose_name_plural = 'CRM Outbox Events'
        indexes = [
            models.Index(fields=['tenant', 'status', 'created_at']),
            models.Index(fields=['status', 'attempts']),
        ]

    def __str__(self):
        return f"{self.event_type} [{self.event_id}] ({self.status}) @ {self.tenant_id}"
