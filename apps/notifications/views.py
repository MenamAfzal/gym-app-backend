"""
Notification Engine — ViewSets

API surface for the notification engine:
- CampaignViewSet       → /campaigns/
- NotificationTemplateViewSet → /templates/
- NotificationAutomationViewSet → /automations/
- NotificationGroupViewSet  → /groups/
- NotificationInboxViewSet  → /inbox/
- FCMDeviceViewSet          → /devices/
- NotificationPreferenceView → /preferences/
- TenantNotificationSettingsView → /settings/
"""
import logging
from django.utils import timezone
from django.db import transaction
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend

from .models import (
    FCMDevice, NotificationTemplate, NotificationGroup, NotificationGroupMember,
    NotificationRecurrenceRule, NotificationCampaign, NotificationInbox,
    NotificationPreference, NotificationAutomation, TenantNotificationSettings,
    NotificationStatus, NotificationAudienceType,
)
from .serializers import (
    FCMDeviceRegisterSerializer, FCMDeviceSerializer,
    NotificationTemplateSerializer, NotificationTemplateListSerializer,
    NotificationGroupSerializer, GroupMemberAddSerializer, GroupMemberRemoveSerializer,
    CampaignCreateSerializer, CampaignReadSerializer, CampaignUpdateSerializer,
    NotificationInboxSerializer, NotificationInboxListSerializer,
    NotificationPreferenceSerializer,
    NotificationAutomationSerializer,
    TenantNotificationSettingsSerializer,
)
from .permissions import (
    IsOwnerOrManager, IsGymOwnerOnly, IsNotificationRecipient,
    IsOwnDevice, IsOwnPreference,
)
from .services import NotificationService

logger = logging.getLogger(__name__)


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100



class FCMDeviceViewSet(viewsets.GenericViewSet):
    """
    Device registration and removal.
    All authenticated users can register/remove their own devices.
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class   = FCMDeviceSerializer

    @action(detail=False, methods=['post'], url_path='register')
    def register(self, request):
        """POST /devices/register/ — Register or update an FCM device token."""
        serializer = FCMDeviceRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        reg_id    = serializer.validated_data['registration_id']
        platform  = serializer.validated_data.get('platform')
        device_id = serializer.validated_data.get('device_id', '')

        device, created = FCMDevice.objects.update_or_create(
            user=request.user,
            registration_id=reg_id,
            defaults={
                'platform':   platform,
                'device_id':  device_id,
                'active':     True,
                'tenant':     request.tenant,
            },
        )

        return Response(
            FCMDeviceSerializer(device).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def destroy(self, request, pk=None):
        """DELETE /devices/{id}/ — Remove own device."""
        device = FCMDevice.objects.filter(id=pk, user=request.user).first()
        if not device:
            return Response({'detail': 'Device not found.'}, status=status.HTTP_404_NOT_FOUND)
        device.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



class NotificationInboxViewSet(viewsets.ReadOnlyModelViewSet):
    """
    In-app notification inbox for the authenticated user.
    All authenticated users — always scoped to own records only.
    """
    permission_classes = [permissions.IsAuthenticated]
    pagination_class   = StandardPagination

    def get_serializer_class(self):
        if self.action == 'list':
            return NotificationInboxListSerializer
        return NotificationInboxSerializer

    def get_queryset(self):
        return NotificationInbox.objects.filter(
            recipient=self.request.user,
        ).order_by('-created_at')

    @action(detail=True, methods=['post'], url_path='read')
    def mark_read(self, request, pk=None):
        """POST /inbox/{id}/read/ — Mark a single notification as read."""
        inbox_item = NotificationInbox.objects.filter(
            id=pk, recipient=request.user
        ).first()
        if not inbox_item:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not inbox_item.is_read:
            inbox_item.is_read = True
            inbox_item.read_at = timezone.now()
            inbox_item.save(update_fields=['is_read', 'read_at'])

        return Response({'detail': 'Marked as read.'})

    @action(detail=False, methods=['post'], url_path='read-all')
    def mark_all_read(self, request):
        """POST /inbox/read-all/ — Mark all unread as read."""
        count = NotificationInbox.objects.filter(
            recipient=request.user, is_read=False
        ).update(is_read=True, read_at=timezone.now())
        return Response({'detail': f'{count} notifications marked as read.'})

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        """GET /inbox/unread-count/ — Badge count for app icon."""
        count = NotificationInbox.objects.filter(
            recipient=request.user, is_read=False
        ).count()
        return Response({'unread_count': count})



class NotificationCampaignViewSet(viewsets.GenericViewSet):
    """
    Campaign management for Gym Owners and Managers.

    Actions:
        list        GET  /campaigns/
        create      POST /campaigns/
        retrieve    GET  /campaigns/{id}/
        update      PATCH /campaigns/{id}/
        destroy     DELETE /campaigns/{id}/
        send        POST /campaigns/{id}/send/
        cancel      POST /campaigns/{id}/cancel/
        history     GET  /campaigns/history/
        scheduled   GET  /campaigns/scheduled/
        preview     POST /campaigns/audience-preview/
    """
    permission_classes = [IsOwnerOrManager]
    pagination_class   = StandardPagination

    def get_queryset(self):
        return NotificationCampaign.objects.select_related(
            'template', 'audience_group', 'recurrence_rule', 'created_by'
        ).order_by('-created_at')

    def list(self, request):
        """GET /campaigns/ — List all tenant campaigns."""
        qs = self.get_queryset()

        # Optional filters
        campaign_status = request.query_params.get('status')
        notification_type = request.query_params.get('notification_type')
        source = request.query_params.get('source')
        if campaign_status:
            qs = qs.filter(status=campaign_status)
        if notification_type:
            qs = qs.filter(notification_type=notification_type)
        if source:
            qs = qs.filter(source=source)

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(CampaignReadSerializer(page, many=True).data)
        return Response(CampaignReadSerializer(qs, many=True).data)

    def create(self, request):
        """POST /campaigns/ — Create a campaign (DRAFT status)."""
        serializer = CampaignCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        # Resolve audience_users UUIDs to User instances for M2M
        from apps.users.models import User
        user_ids = serializer.validated_data.pop('audience_users', [])
        validated = dict(serializer.validated_data)
        if user_ids:
            validated['audience_users'] = list(User.objects.filter(id__in=user_ids, tenant=request.tenant))

        campaign = NotificationService.create_campaign(
            tenant=request.tenant,
            created_by=request.user,
            validated_data=validated,
        )
        return Response(CampaignReadSerializer(campaign).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='audience-preview')
    def audience_preview(self, request):
        """POST /campaigns/audience-preview/ — Preview recipient count before creating."""
        serializer = CampaignCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        audience_type = serializer.validated_data['audience_type']
        
        if audience_type == NotificationAudienceType.SPECIFIC_USERS:
            user_ids = serializer.validated_data.get('audience_users', [])
            from apps.users.models import User
            count = User.objects.filter(id__in=user_ids, tenant=request.tenant, is_active=True).count()
            return Response({'estimated_recipients': count})

        from apps.notifications.audience import AudienceResolver
        mock_campaign = NotificationCampaign(
            tenant=request.tenant,
            audience_type=audience_type,
            audience_group=serializer.validated_data.get('audience_group'),
            audience_entity_id=serializer.validated_data.get('audience_entity_id'),
            audience_filter=serializer.validated_data.get('audience_filter', {}),
        )
        count = AudienceResolver.resolve(mock_campaign).count()
        return Response({'estimated_recipients': count})

    def retrieve(self, request, pk=None):
        """GET /campaigns/{id}/ — Campaign detail with delivery stats."""
        campaign = NotificationCampaign.objects.filter(id=pk).first()
        if not campaign:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(CampaignReadSerializer(campaign).data)

    def partial_update(self, request, pk=None):
        """PATCH /campaigns/{id}/ — Update DRAFT campaign."""
        campaign = NotificationCampaign.objects.filter(id=pk).first()
        if not campaign:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if campaign.status != NotificationStatus.DRAFT:
            return Response(
                {'detail': f'Only DRAFT campaigns can be updated. Current status: {campaign.status}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = CampaignUpdateSerializer(
            campaign, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(CampaignReadSerializer(campaign).data)

    def destroy(self, request, pk=None):
        """DELETE /campaigns/{id}/ — Delete DRAFT campaign."""
        campaign = NotificationCampaign.objects.filter(id=pk).first()
        if not campaign:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if campaign.status != NotificationStatus.DRAFT:
            return Response(
                {'detail': 'Only DRAFT campaigns can be deleted.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        campaign.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='send')
    def send(self, request, pk=None):
        """POST /campaigns/{id}/send/ — Trigger immediate or scheduled delivery."""
        campaign = NotificationCampaign.objects.filter(id=pk).first()
        if not campaign:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            NotificationService.send_campaign(campaign)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {'detail': f'Campaign enqueued. Status: {campaign.status}', 'status': campaign.status}
        )

    @action(detail=True, methods=['post'], url_path='cancel')
    def cancel(self, request, pk=None):
        """POST /campaigns/{id}/cancel/ — Cancel a SCHEDULED campaign."""
        campaign = NotificationCampaign.objects.filter(id=pk).first()
        if not campaign:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            NotificationService.cancel_campaign(campaign)
        except ValueError as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Campaign cancelled.', 'status': campaign.status})

    @action(detail=False, methods=['get'], url_path='history')
    def history(self, request):
        """GET /campaigns/history/ — Sent campaigns with optional filters."""
        qs = self.get_queryset().filter(
            status__in=[
                NotificationStatus.SENT,
                NotificationStatus.PARTIALLY_SENT,
                NotificationStatus.FAILED,
            ]
        )
        notification_type = request.query_params.get('notification_type')
        source = request.query_params.get('source')
        date_from = request.query_params.get('date_from')
        date_to   = request.query_params.get('date_to')
        if notification_type:
            qs = qs.filter(notification_type=notification_type)
        if source:
            qs = qs.filter(source=source)
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(CampaignReadSerializer(page, many=True).data)
        return Response(CampaignReadSerializer(qs, many=True).data)

    @action(detail=False, methods=['get'], url_path='scheduled')
    def scheduled(self, request):
        """GET /campaigns/scheduled/ — Upcoming scheduled campaigns."""
        qs = self.get_queryset().filter(
            status=NotificationStatus.SCHEDULED,
        ).order_by('next_run_at')

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(CampaignReadSerializer(page, many=True).data)
        return Response(CampaignReadSerializer(qs, many=True).data)



class NotificationTemplateViewSet(viewsets.ModelViewSet):
    """Template management for Gym Owners and Managers."""
    permission_classes = [IsOwnerOrManager]
    pagination_class   = StandardPagination

    def get_queryset(self):
        return NotificationTemplate.objects.filter(is_active=True).order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'list':
            return NotificationTemplateListSerializer
        return NotificationTemplateSerializer

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant, created_by=self.request.user)



class NotificationGroupViewSet(viewsets.ModelViewSet):
    """Notification group management for Gym Owners and Managers."""
    permission_classes = [IsOwnerOrManager]
    serializer_class   = NotificationGroupSerializer
    pagination_class   = StandardPagination

    def get_queryset(self):
        return NotificationGroup.objects.order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant, created_by=self.request.user)

    @action(detail=True, methods=['post'], url_path='members')
    def add_member(self, request, pk=None):
        """POST /groups/{id}/members/ — Add a user to the group."""
        group = NotificationGroup.objects.filter(id=pk).first()
        if not group:
            return Response({'detail': 'Group not found.'}, status=status.HTTP_404_NOT_FOUND)

        serializer = GroupMemberAddSerializer(
            data=request.data, context={'request': request, 'group': group}
        )
        serializer.is_valid(raise_exception=True)

        user_id = serializer.validated_data['user_id']
        from apps.users.models import User
        user = User.objects.filter(id=user_id, tenant=request.tenant).first()
        if not user:
            return Response({'detail': 'User not found in this tenant.'}, status=status.HTTP_404_NOT_FOUND)

        _, created = NotificationGroupMember.objects.get_or_create(group=group, user=user)
        if created:
            return Response({'detail': f'{user.email} added to group.'}, status=status.HTTP_201_CREATED)
        return Response({'detail': f'{user.email} is already a member.'})

    @action(detail=True, methods=['delete'], url_path='members/(?P<user_id>[^/.]+)')
    def remove_member(self, request, pk=None, user_id=None):
        """DELETE /groups/{id}/members/{user_id}/ — Remove a user from the group."""
        group = NotificationGroup.objects.filter(id=pk).first()
        if not group:
            return Response({'detail': 'Group not found.'}, status=status.HTTP_404_NOT_FOUND)

        deleted, _ = NotificationGroupMember.objects.filter(
            group=group, user_id=user_id
        ).delete()
        if deleted:
            return Response({'detail': 'Member removed.'}, status=status.HTTP_204_NO_CONTENT)
        return Response({'detail': 'Member not found.'}, status=status.HTTP_404_NOT_FOUND)



class NotificationAutomationViewSet(viewsets.ModelViewSet):
    """Automation management — Gym Owners only."""
    permission_classes = [IsGymOwnerOnly]
    serializer_class   = NotificationAutomationSerializer
    pagination_class   = StandardPagination

    def get_queryset(self):
        return NotificationAutomation.objects.select_related('template').order_by('event_trigger')

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant, created_by=self.request.user)



class NotificationPreferenceView(APIView):
    """
    GET  /preferences/ — Get own notification preferences
    PATCH /preferences/ — Update own notification preferences
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        pref = NotificationPreference.get_or_create_for_user(request.user)
        return Response(NotificationPreferenceSerializer(pref).data)

    def patch(self, request):
        pref = NotificationPreference.get_or_create_for_user(request.user)
        serializer = NotificationPreferenceSerializer(pref, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(NotificationPreferenceSerializer(pref).data)



class TenantNotificationSettingsView(APIView):
    """
    GET  /settings/ — Get tenant notification settings
    PATCH /settings/ — Update tenant notification settings (Gym Owner only)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.request.method in ['PATCH', 'PUT']:
            return [IsGymOwnerOnly()]
        return [permissions.IsAuthenticated()]

    def get(self, request):
        settings_obj = TenantNotificationSettings.get_or_create_for_tenant(request.tenant)
        return Response(TenantNotificationSettingsSerializer(settings_obj).data)

    def patch(self, request):
        settings_obj = TenantNotificationSettings.get_or_create_for_tenant(request.tenant)
        serializer = TenantNotificationSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(TenantNotificationSettingsSerializer(settings_obj).data)
