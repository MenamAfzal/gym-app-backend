from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView
from apps.users.views import (
    ChangePasswordView,
    RegistrationInitView,
    UserViewSet, 
    CustomTokenObtainPairView, 
    UserRegistrationView,
    VerifyOTPAndRegisterView
)

router = DefaultRouter()
router.register(r'profiles', UserViewSet, basename='users')

urlpatterns = [
    # Custom Auth Routes
    path('auth/register/init/', RegistrationInitView.as_view(), name='register_init'),
    path('auth/register/', UserRegistrationView.as_view(), name='auth_register'),
    path('auth/register/verify/', VerifyOTPAndRegisterView.as_view(), name='register_verify'),
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='auth_login'),
    
    # Standard JWT Helper Routes (Refresh/Verify)
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/verify/', TokenVerifyView.as_view(), name='token_verify'),

    # Change Password Route
    path('auth/change-password/', ChangePasswordView.as_view(), name='change_password'),
    
    # ViewSets
    path('', include(router.urls)),
]
