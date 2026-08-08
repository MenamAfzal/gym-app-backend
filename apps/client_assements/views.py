from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, viewsets
from .models import AssessmentSession
from .serializers import AssessmentSessionSerializer
from apps.scheduling.permissions import IsGymStaffOrOwner as IsStaffUser, IsClient as IsClientUser
from apps.users.models import User


class AssessmentSessionAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get(self, request):
        """
        Staff Can see all AssessmentSessions of one user
        List all assessment sessions of the logged-in user.
        """
        user = request.query_params.get('user')
        user = User.objects.filter(email=user).first()
        sessions = AssessmentSession.objects.filter(user=user).order_by('-created_at')
        serializer = AssessmentSessionSerializer(sessions, many=True, context={'request': request})
        return Response(serializer.data)

    def post(self, request):
        """
        Create a new assessment session with sub-assessments.
        """
        data = request.data.copy()
        user = data.get('user')
        user = User.objects.filter(email=user).first()
        data['user'] = user.id
        serializer = AssessmentSessionSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            serializer.save(user=user, assigned_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AssessmentSessionDetailAPIView(APIView):
    permission_classes = [IsAuthenticated, IsStaffUser]

    def get_object(self, pk):
        try:
            return AssessmentSession.objects.get(pk=pk)
        except AssessmentSession.DoesNotExist:
            return None

    def delete(self, request, pk):
        session = self.get_object(pk)
        if session is None:
            return Response({"detail": "Not found AssessmentSession."}, status=status.HTTP_404_NOT_FOUND)
        session.assessments.all().delete()
        session.delete()
        return Response({"detail": "Assessment session deleted."}, status=status.HTTP_204_NO_CONTENT)



class LatestAssessmentSessionViewSet(viewsets.ModelViewSet):
    """

        Client Can view Latest Assessment Sessions

    """
    serializer_class = AssessmentSessionSerializer
    permission_classes = [IsAuthenticated, IsClientUser]

    def get_queryset(self):
        latest_session = (
            AssessmentSession.objects
            .filter(user=self.request.user)
            .order_by('-created_at')
            .first()
        )

        if latest_session:
            return AssessmentSession.objects.filter(id=latest_session.id).prefetch_related('assessments')
        return AssessmentSession.objects.none()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
