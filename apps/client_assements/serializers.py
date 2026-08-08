# serializers.py

from rest_framework import serializers
from .models import AssessmentSession, ClientAssessments
from django.contrib.auth import get_user_model
from apps.users.models import UserProfile

User = get_user_model()


class ClientAssessmentsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientAssessments
        exclude = ['assessment_session', 'tenant']


class AssessmentSessionSerializer(serializers.ModelSerializer):
    assessments = ClientAssessmentsSerializer(many=True)

    class Meta:
        model = AssessmentSession
        fields = ['id', 'user', 'user_level', 'created_at', 'assessments']
        read_only_fields = ['user', 'created_at']

    def create(self, validated_data):
        request = self.context.get('request')
        tenant = getattr(request, 'tenant', None) if request else None
        if not tenant and request and request.user:
            tenant = getattr(request.user, 'tenant', None)

        assessments_data = validated_data.pop('assessments')
        session = AssessmentSession.objects.create(tenant=tenant, **validated_data)
        
        user_profile = UserProfile.objects.get(user=session.user)
        user_profile.level = session.user_level
        user_profile.save()
        
        for assessment_data in assessments_data:
            ClientAssessments.objects.create(assessment_session=session, tenant=tenant, **assessment_data)
        return session

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['user'] = {
            "id": instance.user.id,
            "email": instance.user.email
        }
        if instance.assigned_by:
            data['assigned_by'] = {
                "id": instance.assigned_by.id,
                "email": instance.assigned_by.email,
                "name": instance.assigned_by.profile.first_name + " " + instance.assigned_by.profile.last_name
            }
        else:
            data['assigned_by'] = None
        return data
