from rest_framework import status, permissions, generics
from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils import timezone
from django.shortcuts import get_object_or_404
from .models import Notification, FCMDevice
from .serializers import FCMDeviceSerializer, NotificationSerializer

class FCMDeviceRegisterAPIView(APIView):
    """
    POST: Register or update an FCM token for the authenticated user.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = FCMDeviceSerializer(data=request.data)
        if serializer.is_valid():
            reg_id = serializer.validated_data['registration_id']
            device_id = serializer.validated_data.get('device_id')
            device_type = serializer.validated_data.get('device_type')
            
            device, created = FCMDevice.objects.get_or_create(
                user=request.user,
                registration_id=reg_id,
                defaults={
                    'device_id': device_id,
                    'device_type': device_type,
                    'active': True
                }
            )
            
            if not created:
                # Update existing device info
                device.device_id = device_id
                device.device_type = device_type
                device.active = True
                device.save()
            
            return Response(
                {"detail": "Device registered successfully.", "created": created},
                status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class NotificationListAPIView(generics.ListAPIView):
    """
    GET: Retrieve list of notifications for the authenticated user.
    """
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).order_by('-created_at')

class NotificationMarkReadAPIView(APIView):
    """
    POST: Mark a specific notification as read, or mark all as read.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk=None):
        if pk:
            # Mark specific notification
            notification = get_object_or_404(Notification, id=pk, user=request.user)
            if not notification.is_read:
                notification.is_read = True
                notification.read_at = timezone.now()
                notification.save()
            return Response({"detail": "Notification marked as read."}, status=status.HTTP_200_OK)
        else:
            # Mark all as read
            unread_notifications = Notification.objects.filter(user=request.user, is_read=False)
            count = unread_notifications.update(is_read=True, read_at=timezone.now())
            return Response({"detail": f"{count} notifications marked as read."}, status=status.HTTP_200_OK)
