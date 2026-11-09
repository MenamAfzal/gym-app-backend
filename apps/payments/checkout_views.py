from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.payments.models import ExternalCheckoutSession
from apps.payments.checkout_serializers import (
    CheckoutCompletionSerializer,
    CheckoutSessionDetailSerializer,
    CreateCheckoutSessionSerializer,
    PublicGymInfoSerializer,
    PublicPackageSerializer,
    SetCustomerPasswordSerializer,
    VerifySessionOTPSerializer,
)
from apps.payments.checkout_service import CheckoutIntegrationService


def get_session_token_from_request(request):
    token = request.headers.get('X-Checkout-Token') or request.META.get('HTTP_X_CHECKOUT_TOKEN')
    if not token and 'Authorization' in request.headers:
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header[7:].strip()
    if not token:
        token = request.query_params.get('token')
    return token


class CheckoutRateThrottle(AnonRateThrottle):
    rate = '10/minute'


class CheckoutOTPSendThrottle(AnonRateThrottle):
    rate = '5/minute'


class PublicGymInfoView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, tenant_identifier):
        origin = request.headers.get('Origin')
        tenant = CheckoutIntegrationService.get_public_gym_info(tenant_identifier, origin=origin)
        serializer = PublicGymInfoSerializer(tenant, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class PublicGymPackagesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, tenant_identifier):
        origin = request.headers.get('Origin')
        location_id = request.query_params.get('location_id')
        packages = CheckoutIntegrationService.get_available_packages(
            tenant_identifier=tenant_identifier,
            location_id=location_id,
            origin=origin
        )
        serializer = PublicPackageSerializer(packages, many=True, context={'request': request})
        return Response({'packages': serializer.data}, status=status.HTTP_200_OK)


class CreateCheckoutSessionView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CheckoutRateThrottle]

    def post(self, request):
        serializer = CreateCheckoutSessionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        origin = request.headers.get('Origin')
        result = CheckoutIntegrationService.create_checkout_session(
            validated_data=serializer.validated_data,
            origin=origin
        )
        session = result['session']
        session_token = result['session_token']

        detail_serializer = CheckoutSessionDetailSerializer(session, context={'request': request})
        response_data = detail_serializer.data
        response_data['session_token'] = session_token
        response_data['otp'] = {
            'expires_in_seconds': 300,
            'cooldown_seconds': 60,
            'detail': f'Verification code sent to {session.email}.',
        }
        return Response(response_data, status=status.HTTP_201_CREATED)


class SessionResendOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CheckoutOTPSendThrottle]

    def post(self, request, session_id):
        token = get_session_token_from_request(request)
        session = CheckoutIntegrationService.authenticate_session(session_id, token)
        result = CheckoutIntegrationService.resend_session_otp(session)
        return Response(result, status=status.HTTP_200_OK)


class SessionVerifyOTPView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CheckoutRateThrottle]

    def post(self, request, session_id):
        token = get_session_token_from_request(request)
        session = CheckoutIntegrationService.authenticate_session(session_id, token)
        serializer = VerifySessionOTPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        verified_session = CheckoutIntegrationService.verify_session_otp(
            session=session,
            otp_code=serializer.validated_data['otp_code']
        )
        return Response({
            'session_id': str(verified_session.id),
            'status': verified_session.status,
            'is_verified': verified_session.is_verified,
            'can_proceed': verified_session.can_proceed,
            'detail': 'Email verified successfully. You may now proceed to payment.',
        }, status=status.HTTP_200_OK)


class CheckoutSessionDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, session_id):
        token = get_session_token_from_request(request)
        session = CheckoutIntegrationService.authenticate_session(session_id, token)
        serializer = CheckoutSessionDetailSerializer(session, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class InitiateSessionPaymentView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CheckoutRateThrottle]

    def post(self, request, session_id):
        token = get_session_token_from_request(request)
        session = CheckoutIntegrationService.authenticate_session(session_id, token)
        return_url = request.data.get('return_url')
        cancel_url = request.data.get('cancel_url')
        result = CheckoutIntegrationService.initiate_session_payment(
            session=session,
            return_url=return_url,
            cancel_url=cancel_url
        )
        return Response(result, status=status.HTTP_200_OK)


class SessionPaymentStatusView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, session_id):
        token = get_session_token_from_request(request)
        session = CheckoutIntegrationService.authenticate_session(session_id, token)
        return Response({
            'session_id': str(session.id),
            'payment_status': session.payment_status,
            'checkout_status': session.status,
            'is_completed': session.status == ExternalCheckoutSession.StatusChoices.COMPLETED,
            'completed_at': session.completed_at,
        }, status=status.HTTP_200_OK)


class CheckoutSessionCompletionView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, session_id):
        token = get_session_token_from_request(request)
        session = CheckoutIntegrationService.authenticate_session(session_id, token)
        if session.status != ExternalCheckoutSession.StatusChoices.COMPLETED:
            return Response({
                'session_id': str(session.id),
                'payment_status': session.payment_status,
                'checkout_status': session.status,
                'is_completed': False,
                'detail': 'Payment is still being processed or was not completed.',
            }, status=status.HTTP_200_OK)

        serializer = CheckoutCompletionSerializer(session, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class SessionSetPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request, session_id):
        token = get_session_token_from_request(request)
        session = CheckoutIntegrationService.authenticate_session(session_id, token)
        serializer = SetCustomerPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = CheckoutIntegrationService.set_session_customer_password(
            session=session,
            new_password=serializer.validated_data['new_password']
        )
        return Response(result, status=status.HTTP_200_OK)
