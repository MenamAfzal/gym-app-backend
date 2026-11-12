from django.http import HttpResponse
from django.template.loader import render_to_string
from rest_framework import status
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.payments.models import ExternalCheckoutSession
from apps.payments.checkout_serializers import (
    AcceptHealthCommitmentSerializer,
    CheckoutCompletionSerializer,
    CheckoutSessionDetailSerializer,
    CreateCheckoutSessionSerializer,
    PublicGymInfoSerializer,
    PublicPackageSerializer,
    SetCustomerPasswordSerializer,
    VerifySessionOTPSerializer,
)
from apps.payments.checkout_service import CheckoutIntegrationService


def _extract_client_metadata(request):
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        ip_address = forwarded_for.split(',')[0].strip()
    else:
        ip_address = request.META.get('REMOTE_ADDR')
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    return ip_address, user_agent


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
        ip_address, user_agent = _extract_client_metadata(request)
        result = CheckoutIntegrationService.create_checkout_session(
            validated_data=serializer.validated_data,
            origin=origin,
            ip_address=ip_address,
            user_agent=user_agent
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


class SessionHealthCommitmentView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CheckoutRateThrottle]

    def post(self, request, session_id):
        token = get_session_token_from_request(request)
        session = CheckoutIntegrationService.authenticate_session(session_id, token)
        serializer = AcceptHealthCommitmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ip_address, user_agent = _extract_client_metadata(request)
        result = CheckoutIntegrationService.accept_health_commitment(
            session=session,
            accepted=serializer.validated_data['accepted'],
            signer_name=serializer.validated_data.get('signer_name', ''),
            ip_address=ip_address,
            user_agent=user_agent
        )
        return Response(result, status=status.HTTP_200_OK)


class InitiateSessionPaymentView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [CheckoutRateThrottle]

    def post(self, request, session_id):
        token = get_session_token_from_request(request)
        session = CheckoutIntegrationService.authenticate_session(session_id, token)
        return_url = request.data.get('return_url')
        cancel_url = request.data.get('cancel_url')
        health_commitment_accepted = request.data.get('health_commitment_accepted')
        signer_name = request.data.get('health_commitment_signer_name', '')
        ip_address, user_agent = _extract_client_metadata(request)
        result = CheckoutIntegrationService.initiate_session_payment(
            session=session,
            return_url=return_url,
            cancel_url=cancel_url,
            health_commitment_accepted=bool(health_commitment_accepted) if health_commitment_accepted is not None else None,
            signer_name=signer_name,
            ip_address=ip_address,
            user_agent=user_agent
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


class _IgnoreFormatQueryNegotiation(DefaultContentNegotiation):
    def select_renderer(self, request, renderers, format_suffix=None):
        return (renderers[0], renderers[0].media_type)


class SessionSignedAgreementView(APIView):
    permission_classes = [AllowAny]
    content_negotiation_class = _IgnoreFormatQueryNegotiation

    def get(self, request, session_id):
        access_token = (
            request.query_params.get('access')
            or request.query_params.get('signature')
            or ''
        ).strip() or None
        checkout_token = get_session_token_from_request(request)
        session = CheckoutIntegrationService.authorize_agreement_access(
            session_id=session_id,
            access_token=access_token,
            checkout_token=checkout_token,
        )
        agreement = CheckoutIntegrationService.get_signed_agreement_document(session)

        base_url = request.build_absolute_uri('/').rstrip('/')
        resolved_token = access_token or CheckoutIntegrationService.generate_agreement_access_token(session.id)
        view_url = f'{base_url}/api/v1/checkout/sessions/{session.id}/agreement/?access={resolved_token}&format=html'
        download_url = f'{view_url}&download=1'

        requested_format = (request.query_params.get('format') or '').strip().lower()
        is_download = (request.query_params.get('download') or '').strip().lower() in ('1', 'true', 'yes')
        if requested_format == 'html' or is_download:
            html = render_to_string('payments/signed_agreement.html', {
                'agreement': agreement,
                'download_url': download_url,
                'is_download': is_download,
            })
            response = HttpResponse(html, content_type='text/html; charset=utf-8')
            if is_download:
                response['Content-Disposition'] = f'attachment; filename="signed-agreement-{session.id}.html"'
            return response

        return Response({
            **agreement,
            'view_url': view_url,
            'download_url': download_url,
        }, status=status.HTTP_200_OK)
