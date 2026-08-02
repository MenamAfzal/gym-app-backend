from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.socialnetwork.views import (
    CommentViewSet,
    MediaViewSet,
    MultiMediaUploadAPIView,
    PollAPIView,
    PollCreateAPIView,
    UnifiedFeedAPIView,
    UnifiedMediaUploadAPIView,
)

router = DefaultRouter()
router.register(r'polls', PollAPIView, basename='poll')
router.register(r'media', MediaViewSet, basename='media')
router.register(r'comments', CommentViewSet, basename='comment_reactions')


client_urlpatterns= [
    
]
staff_urlpatterns= [
    path("upload-poll", PollCreateAPIView.as_view(), name="upload-poll"),
    path("upload", MultiMediaUploadAPIView.as_view(), name="upload-media"), 
    path("upload-unified", UnifiedMediaUploadAPIView.as_view(), name="unified-media-upload"),
]
shared_urlpatterns= [
    path('', include(router.urls)),
    path('feed/', UnifiedFeedAPIView.as_view(), name='unified-feed'),
]
urlpatterns = client_urlpatterns + staff_urlpatterns + shared_urlpatterns
