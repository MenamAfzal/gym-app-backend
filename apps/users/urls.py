from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView
from apps.users.views import (
    ChangePasswordView,
    RegistrationInitView,
    UserViewSet, 
    CustomTokenObtainPairView, 
    UserRegistrationView,
    VerifyOTPAndRegisterView,
    ForgotPasswordInitView,
    ForgotPasswordVerifyView
)

router = DefaultRouter()
router.register(r'profiles', UserViewSet, basename='users')

urlpatterns = [
    # Custom Auth Routes
    path('auth/register/init/', RegistrationInitView.as_view(), name='register_init'),
    path('auth/register/', UserRegistrationView.as_view(), name='auth_register'),
    path('auth/register/verify/', VerifyOTPAndRegisterView.as_view(), name='register_verify'),
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='auth_login'),
    
    # Forgot Password Routes
    path('auth/forgot-password/init/', ForgotPasswordInitView.as_view(), name='forgot_password_init'),
    path('auth/forgot-password/verify/', ForgotPasswordVerifyView.as_view(), name='forgot_password_verify'),
    
    # Standard JWT Helper Routes (Refresh/Verify)
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh_alt'),
    path('auth/verify/', TokenVerifyView.as_view(), name='token_verify'),
    path('auth/token/verify/', TokenVerifyView.as_view(), name='token_verify_alt'),

    # Change Password Route
    path('auth/change-password/', ChangePasswordView.as_view(), name='change_password'),
    
    # ViewSets
    path('', include(router.urls)),
]
