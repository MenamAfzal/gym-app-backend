from django.contrib import admin
from .models import ClientAssessments, AssessmentSession

# Register your models here.
admin.site.register(ClientAssessments)
admin.site.register(AssessmentSession)
