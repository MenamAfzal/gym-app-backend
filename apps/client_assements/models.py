# assessments/models.py
from django.db import models
from django.conf import settings
from core_models.mixins.tenant_mixin import TenantMixin

class AssessmentSession(TenantMixin):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="assessment_sessions"
    )
    user_level = models.CharField(max_length=100, null=True, blank=True, default="Rx1")
    created_at = models.DateTimeField(auto_now_add=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_sessions'
    )

    def __str__(self):
        return f"{self.user.email} - {self.created_at}"


class ClientAssessments(TenantMixin):
    assessment_session = models.ForeignKey(AssessmentSession, on_delete=models.CASCADE, related_name='assessments')
    assessment_title = models.CharField(max_length=100, null=True, blank=True)
    assessment_left_value = models.CharField(max_length=100, null=True, blank=True)
    assessment_right_value = models.CharField(max_length=100, null=True, blank=True)
    assessment_final_value = models.CharField(max_length=100, null=True, blank=True)
    assessment_raw_value = models.CharField(max_length=100, null=True, blank=True)
    assessment_comment = models.CharField(max_length=100, null=True, blank=True)
    user_level = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.assessment_title or "Unnamed Assessment"