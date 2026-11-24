from django.urls import path
from apps.ghl.views import (
    GHLOAuthConnectView,
    GHLOAuthCallbackView,
    GHLConnectionStatusView,
    GHLDisconnectView,
)

app_name = 'ghl'

urlpatterns = [
    path('oauth/connect/', GHLOAuthConnectView.as_view(), name='oauth-connect'),
    path('oauth/callback/', GHLOAuthCallbackView.as_view(), name='oauth-callback'),
    path('status/', GHLConnectionStatusView.as_view(), name='connection-status'),
    path('disconnect/', GHLDisconnectView.as_view(), name='disconnect'),
]
