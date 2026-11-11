from .base import *
import os

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
DEBUG = True
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(",")

# Database
# Parse database connection url strings like psql://user:pass@127.0.0.1:8458/db
# We assume the user creates the DB config via DJANGO_DATABASE_URL or manually
import dj_database_url
DATABASES = {
    'default': dj_database_url.config(
        default=os.environ.get("DATABASE_URL"),
        conn_max_age=600
    )
}

# Celery (Prod)
CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
if EMAIL_BACKEND:
    EMAIL_BACKEND = EMAIL_BACKEND.strip("'\"")
EMAIL_HOST = os.environ.get('EMAIL_HOST')
if EMAIL_HOST:
    EMAIL_HOST = EMAIL_HOST.strip("'\"")
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
_env_ssl = os.environ.get('EMAIL_USE_SSL')
_env_tls = os.environ.get('EMAIL_USE_TLS')
if _env_ssl is not None:
    EMAIL_USE_SSL = _env_ssl.strip("'\"").lower() == 'true'
else:
    EMAIL_USE_SSL = EMAIL_PORT == 465
if EMAIL_USE_SSL:
    EMAIL_USE_TLS = False
elif _env_tls is not None:
    EMAIL_USE_TLS = _env_tls.strip("'\"").lower() == 'true'
else:
    EMAIL_USE_TLS = EMAIL_PORT == 587
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER')
if EMAIL_HOST_USER:
    EMAIL_HOST_USER = EMAIL_HOST_USER.strip("'\"")
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD')
if EMAIL_HOST_PASSWORD:
    EMAIL_HOST_PASSWORD = EMAIL_HOST_PASSWORD.strip("'\"")
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', default=EMAIL_HOST_USER)
if DEFAULT_FROM_EMAIL:
    DEFAULT_FROM_EMAIL = DEFAULT_FROM_EMAIL.strip("'\"")
EMAIL_TIMEOUT = int(os.environ.get('EMAIL_TIMEOUT', 10))

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://redis:6379/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    }
}

CSRF_TRUSTED_ORIGINS = [f"http://{host}" for host in ALLOWED_HOSTS if host] + [os.environ.get("FRONTEND_URL", "")]
CSRF_TRUSTED_ORIGINS = [origin for origin in CSRF_TRUSTED_ORIGINS if origin]

# 2. Disable SSL requirements (Since we don't have a domain/cert yet)
SECURE_SSL_REDIRECT = False

# 3. Allow Cookies over HTTP (Fixes the "Login Loop")
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
