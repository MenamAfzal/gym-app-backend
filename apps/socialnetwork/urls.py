from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.socialnetwork.views import (
    CommentViewSet,
    MediaViewSet,
    MultiMediaUploadAPIView,
    PollAPIView,
    PollCreateAPIView,
    PostViewSet,
    UnifiedFeedAPIView,
    UnifiedMediaUploadAPIView,
    UserBlockViewSet,
    UserBlockActionView,
    UserUnblockActionView,
    AdminUserBlockViewSet,
    PostReportViewSet,
    AdminPostReportViewSet,
)

router = DefaultRouter()
router.register(r'polls', PollAPIView, basename='poll')
router.register(r'media', MediaViewSet, basename='media')
router.register(r'comments', CommentViewSet, basename='comment_reactions')
router.register(r'posts', PostViewSet, basename='post')
router.register(r'blocks', UserBlockViewSet, basename='user-block')
router.register(r'reports', PostReportViewSet, basename='post-report')
router.register(r'admin/blocks', AdminUserBlockViewSet, basename='admin-user-block')
router.register(r'admin/reports', AdminPostReportViewSet, basename='admin-post-report')


client_urlpatterns = [
    
]
staff_urlpatterns = [
    path("upload-poll", PollCreateAPIView.as_view(), name="upload-poll"),
    path("upload", MultiMediaUploadAPIView.as_view(), name="upload-media"), 
    path("upload-unified", UnifiedMediaUploadAPIView.as_view(), name="unified-media-upload"),
]
shared_urlpatterns = [
    path('', include(router.urls)),
    path('feed/', UnifiedFeedAPIView.as_view(), name='unified-feed'),
    path('users/<uuid:user_id>/block/', UserBlockActionView.as_view(), name='user-block-action'),
    path('users/<uuid:user_id>/unblock/', UserUnblockActionView.as_view(), name='user-unblock-action'),
]
urlpatterns = client_urlpatterns + staff_urlpatterns + shared_urlpatterns

