import hashlib
import time
import requests
from apps.payments.models import ExternalCheckoutSession


class MetaConversionsService:
    @staticmethod
    def _hash_field(val):
        if not val:
            return None
        clean_val = str(val).strip().lower()
        return hashlib.sha256(clean_val.encode('utf-8')).hexdigest()

    @staticmethod
    def _hash_phone(val):
        if not val:
            return None
        digits = ''.join(c for c in str(val) if c.isdigit())
        return hashlib.sha256(digits.encode('utf-8')).hexdigest()

    @classmethod
    def send_event(cls, session_id, event_name):
        session = ExternalCheckoutSession.all_objects.select_related('tenant').filter(id=session_id).first()
        if not session:
            return

        tenant = session.tenant
        config = getattr(tenant, 'checkout_config', None)
        if not config or not config.meta_pixel_id or not config.meta_capi_access_token:
            return

        pixel_id = config.meta_pixel_id.strip()
        access_token = config.meta_capi_access_token.strip()

        event_id = str(session.purchase_event_id if event_name == 'Purchase' else session.initiate_event_id)

        user_data = {}
        if session.email:
            user_data['em'] = [cls._hash_field(session.email)]
        if session.first_name:
            user_data['fn'] = [cls._hash_field(session.first_name)]
        if session.last_name:
            user_data['ln'] = [cls._hash_field(session.last_name)]
        if session.phone_number:
            user_data['ph'] = [cls._hash_phone(session.phone_number)]
        if session.client_ip_address:
            user_data['client_ip_address'] = session.client_ip_address
        if session.client_user_agent:
            user_data['client_user_agent'] = session.client_user_agent
        if session.fbp:
            user_data['fbp'] = session.fbp
        if session.fbc:
            user_data['fbc'] = session.fbc

        event_payload = {
            'event_name': event_name,
            'event_time': int(time.time()),
            'event_id': event_id,
            'event_source_url': session.source_url or f'https://api.fitverx.com/checkout/{session.id}/',
            'action_source': 'website',
            'user_data': user_data,
            'custom_data': {
                'currency': (session.currency or 'usd').lower(),
                'value': float(session.price_snapshot),
                'content_type': 'product',
                'contents': [{'id': str(session.package_type_id), 'quantity': 1}],
            }
        }

        data_payload = {'data': [event_payload]}
        if config.meta_test_event_code:
            data_payload['test_event_code'] = config.meta_test_event_code.strip()

        url = f'https://graph.facebook.com/v19.0/{pixel_id}/events'
        params = {'access_token': access_token}

        try:
            requests.post(url, json=data_payload, params=params, timeout=5)
        except Exception:
            pass

