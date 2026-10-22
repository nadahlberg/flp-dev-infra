"""Settings overlay for the flp-dev deployment of CourtListener.

Selected with DJANGO_SETTINGS_MODULE=flp_dev_settings (mounted at
/opt/flp-dev, which is on PYTHONPATH). It loads CourtListener's own settings
in production mode and then patches the handful of things production mode
assumes about AWS that this environment provides differently. Everything
else is configured through the environment, exactly as in production.
"""

import environ
from cl.settings import *
from cl.settings import AWS_S3_CUSTOM_DOMAIN as _PRODUCTION_STORAGE_DOMAIN
from cl.settings import CACHES, DATABASES, REST_FRAMEWORK, SECURE_CSP

env = environ.FileAwareEnv()

# Managed Postgres listens on a non-default port and CL's settings only read
# DB_HOST.
DATABASES["default"]["PORT"] = env("DB_PORT", default="25060")

# Static files (and S3-backed media) on DO Spaces through its S3-compatible
# endpoint. CL's storage classes read these django-storages settings.
AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL")
AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME")
AWS_S3_CUSTOM_DOMAIN = env("AWS_S3_CUSTOM_DOMAIN")
AWS_S3_SIGNATURE_VERSION = "s3v4"

# The CSP was built against the production storage domain; swap in ours.
_old = f"https://{_PRODUCTION_STORAGE_DOMAIN}/"
_new = f"https://{AWS_S3_CUSTOM_DOMAIN}/"
for _directive, _sources in SECURE_CSP.items():
    if isinstance(_sources, list):
        SECURE_CSP[_directive] = [_new if s == _old else s for s in _sources]

# No SES here: mail goes to the pod logs. This also covers CL's wrapper
# backend, which would otherwise hand off to django_ses.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
BASE_BACKEND = EMAIL_BACKEND

# Production backs the "s3" cache alias with S3 Express. The URL conf and
# cl.lib.s3_cache reference the alias by name, so it has to exist; here it
# is just another Redis database.
CACHES["s3"] = {
    "BACKEND": "django.core.cache.backends.redis.RedisCache",
    "LOCATION": CACHES["default"]["LOCATION"],
    "OPTIONS": {"db": 4},
}

# One tester, two small nodes.
CELERY_WORKER_CONCURRENCY = env.int("CELERY_WORKER_CONCURRENCY", default=2)
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["anon"] = "10000/day"
