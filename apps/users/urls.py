from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenVerifyView
from apps.users.views import (
    ChangePasswordView,
    RegistrationInitView,
    UserViewSet, 
    CustomTokenObtainPairView,
    CustomTokenRefreshView,
    UserRegistrationView,
    VerifyOTPAndRegisterView,
    ForgotPasswordInitView,
    ForgotPasswordVerifyView,
    StaffRegistrationRequestCreateView,
    StaffRegistrationStatusView,
    StaffRegistrationRequestViewSet
)
from apps.scheduling.views import ClientBookingPreferenceView

router = DefaultRouter()
router.register(r'profiles', UserViewSet, basename='users')
router.register(r'staff-requests', StaffRegistrationRequestViewSet, basename='staff-requests')

urlpatterns = [
    path('auth/register/init/', RegistrationInitView.as_view(), name='register_init'),
    path('auth/register/', UserRegistrationView.as_view(), name='auth_register'),
    path('auth/register/verify/', VerifyOTPAndRegisterView.as_view(), name='register_verify'),
    path('auth/login/', CustomTokenObtainPairView.as_view(), name='auth_login'),
    
    path('auth/staff-register/', StaffRegistrationRequestCreateView.as_view(), name='staff_register'),
    path('auth/staff-register/status/', StaffRegistrationStatusView.as_view(), name='staff_register_status'),
    path('staff-register/', StaffRegistrationRequestCreateView.as_view(), name='staff_register_alt'),
    path('staff-register/status/', StaffRegistrationStatusView.as_view(), name='staff_register_status_alt'),
    
    path('auth/forgot-password/init/', ForgotPasswordInitView.as_view(), name='forgot_password_init'),
    path('auth/forgot-password/verify/', ForgotPasswordVerifyView.as_view(), name='forgot_password_verify'),
    
    path('auth/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('auth/token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh_alt'),
    path('auth/verify/', TokenVerifyView.as_view(), name='token_verify'),
    path('auth/token/verify/', TokenVerifyView.as_view(), name='token_verify_alt'),

    path('auth/change-password/', ChangePasswordView.as_view(), name='change_password'),
    
    path('profiles/deactivate/', UserViewSet.as_view({'post': 'self_deactivate'}), name='users-self-deactivate'),
    path('profiles/me/deactivate/', UserViewSet.as_view({'post': 'self_deactivate'}), name='users-me-deactivate'),
    path('profiles/activate/', UserViewSet.as_view({'post': 'activate_user_list'}), name='users-activate-list'),
    path('profiles/me/activate/', UserViewSet.as_view({'post': 'activate'}), name='users-me-activate'),

    path('booking-preferences/', ClientBookingPreferenceView.as_view(), name='user-booking-preferences'),
    path('permissions/catalog/', UserViewSet.as_view({'get': 'permissions_catalog'}), name='permissions-catalog'),
    path('managers/my-permissions/', UserViewSet.as_view({'get': 'my_permissions'}), name='my-permissions'),
    path('managers/<uuid:pk>/permissions/', UserViewSet.as_view({'get': 'manager_permissions', 'put': 'manager_permissions', 'patch': 'manager_permissions'}), name='manager-permissions-detail'),
    path('<uuid:pk>/manager-permissions/', UserViewSet.as_view({'get': 'manager_permissions', 'put': 'manager_permissions', 'patch': 'manager_permissions'}), name='user-manager-permissions'),
    path('', include(router.urls)),
]

