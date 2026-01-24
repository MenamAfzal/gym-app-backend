from .base import *
import os

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
DEBUG = False
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


CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://redis:6379/1",
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    }
}

CSRF_TRUSTED_ORIGINS = ['http://3.147.66.56']

# 2. Disable SSL requirements (Since we don't have a domain/cert yet)
SECURE_SSL_REDIRECT = False

# 3. Allow Cookies over HTTP (Fixes the "Login Loop")
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
