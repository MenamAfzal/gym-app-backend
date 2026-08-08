import os
import json
import logging
import requests
from django.conf import settings
from .models import Notification, FCMDevice

logger = logging.getLogger(__name__)

class FirebaseNotificationService:
    """
    Custom Firebase Notification Service using FCM HTTP v1 API.
    To use this, ensure `google-auth` and `requests` are installed.
    You will need to provide the path to your Firebase service account JSON file.
    """
    
    def __init__(self):
       
        self.credentials_path = getattr(settings, 'FIREBASE_CREDENTIALS_PATH', os.path.join(settings.BASE_DIR, 'firebase-credentials.json'))
        self.project_id = self._get_project_id()

    def _get_project_id(self):
        try:
            if os.path.exists(self.credentials_path):
                with open(self.credentials_path, 'r') as f:
                    data = json.load(f)
                    return data.get('project_id')
        except Exception as e:
            logger.error(f"Error reading Firebase credentials: {e}")
        return None

    def _get_access_token(self):
        """
        Gets an OAuth2 access token for the FCM API.
        Requires `google-auth` library: pip install google-auth
        """
        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests
            
            scopes = ['https://www.googleapis.com/auth/firebase.messaging']
            credentials = service_account.Credentials.from_service_account_file(
                self.credentials_path, scopes=scopes
            )
            request = google.auth.transport.requests.Request()
            credentials.refresh(request)
            return credentials.token
        except ImportError:
            logger.error("google-auth library is not installed. Run `pip install google-auth`")
            return None
        except Exception as e:
            logger.error(f"Error getting Firebase access token: {e}")
            return None

    def send_notification_to_user(self, user, title, body, data=None):
        """
        Main method to send a notification to a specific user.
        1. Saves the notification in the database.
        2. Sends the push notification to all active devices.
        """
        if data is None:
            data = {}

        # 1. Save in database
        notification = Notification.objects.create(
            user=user,
            title=title,
            body=body,
            data=data
        )

        # 2. Find active devices
        devices = FCMDevice.objects.filter(user=user, active=True)
        if not devices.exists():
            return notification

        # 3. Send Push Notification
        success_count = 0
        for device in devices:
            if self.send_fcm_message(device.registration_id, title, body, data):
                success_count += 1

        if success_count > 0:
            notification.push_sent = True
            notification.save()

        return notification

    def send_fcm_message(self, token, title, body, data=None):
        """
        Sends the actual HTTP request to FCM v1 API.
        """
        if not self.project_id:
            logger.warning("Firebase project ID not found. Ensure the credentials JSON is present at FIREBASE_CREDENTIALS_PATH.")
            return False

        access_token = self._get_access_token()
        if not access_token:
            return False

        url = f"https://fcm.googleapis.com/v1/projects/{self.project_id}/messages:send"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json; UTF-8',
        }

        # Structure for HTTP v1 API
        payload = {
            "message": {
                "token": token,
                "notification": {
                    "title": title,
                    "body": body,
                },
                "data": {str(k): str(v) for k, v in data.items()} if data else {}
            }
        }

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info(f"Successfully sent notification to {token}")
                return True
            else:
                logger.error(f"Failed to send notification: {response.status_code} - {response.text}")
                # Optional: If error is UNREGISTERED (token invalid), you could deactivate the device here
                if response.status_code == 404 or "UNREGISTERED" in response.text:
                    self._deactivate_token(token)
                return False
        except Exception as e:
            logger.error(f"Exception sending FCM message: {e}")
            return False

    def _deactivate_token(self, token):
        try:
            FCMDevice.objects.filter(registration_id=token).update(active=False)
        except Exception as e:
            pass
