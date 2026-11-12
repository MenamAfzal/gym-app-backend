from django.urls import path
from apps.payments.checkout_views import (
    CheckoutSessionCompletionView,
    CheckoutSessionDetailView,
    CreateCheckoutSessionView,
    InitiateSessionPaymentView,
    PublicGymInfoView,
    PublicGymPackagesView,
    SessionHealthCommitmentView,
    SessionPaymentStatusView,
    SessionResendOTPView,
    SessionSetPasswordView,
    SessionSignedAgreementView,
    SessionVerifyOTPView,
)

urlpatterns = [
    path('gym/<str:tenant_identifier>/', PublicGymInfoView.as_view(), name='checkout-gym-info'),
    path('gym/<str:tenant_identifier>/packages/', PublicGymPackagesView.as_view(), name='checkout-gym-packages'),
    path('sessions/', CreateCheckoutSessionView.as_view(), name='checkout-create-session'),
    path('sessions/<uuid:session_id>/', CheckoutSessionDetailView.as_view(), name='checkout-session-detail'),
    path('sessions/<uuid:session_id>/otp/resend/', SessionResendOTPView.as_view(), name='checkout-session-otp-resend'),
    path('sessions/<uuid:session_id>/otp/verify/', SessionVerifyOTPView.as_view(), name='checkout-session-otp-verify'),
    path('sessions/<uuid:session_id>/health-commitment/', SessionHealthCommitmentView.as_view(), name='checkout-session-health-commitment'),
    path('sessions/<uuid:session_id>/initiate-payment/', InitiateSessionPaymentView.as_view(), name='checkout-session-initiate-payment'),
    path('sessions/<uuid:session_id>/payment-status/', SessionPaymentStatusView.as_view(), name='checkout-session-payment-status'),
    path('sessions/<uuid:session_id>/completion/', CheckoutSessionCompletionView.as_view(), name='checkout-session-completion'),
    path('sessions/<uuid:session_id>/agreement/', SessionSignedAgreementView.as_view(), name='checkout-session-agreement'),
    path('sessions/<uuid:session_id>/set-password/', SessionSetPasswordView.as_view(), name='checkout-session-set-password'),
]
