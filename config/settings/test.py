from .base import *

SECRET_KEY = 'test-key'
DEBUG = False

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db_test.sqlite3',
    }
}

# Use in-memory cache for testing (mock Redis)
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

ALLOWED_HOSTS = ['*']

CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache'
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_STORE_EAGER_RESULT = False

