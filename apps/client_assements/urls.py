from django.urls import path, include
from rest_framework.routers import DefaultRouter

from apps.client_assements.views import AssessmentSessionAPIView, AssessmentSessionDetailAPIView, \
    LatestAssessmentSessionViewSet

router = DefaultRouter()
router.register(r'view_client_assessments', LatestAssessmentSessionViewSet, basename='client_assessments')


staff_create_assessment_endpoint = [
    path('assessment-sessions/', AssessmentSessionAPIView.as_view(), name='assessment-session-list-create'),
    path('assessment-sessions/<int:pk>/', AssessmentSessionDetailAPIView.as_view(), name='assessment-session-delete'),

]

client_view_assessment_list_endpoint = [
    path('', include(router.urls)),
]

urlpatterns = staff_create_assessment_endpoint + client_view_assessment_list_endpoint

