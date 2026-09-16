"""Production settings: the app runs behind Traefik with an external Postgres.

Secrets and the database URL are supplied through the environment; nothing is
read from a file in the repository.
"""

from .base import *  # noqa: F401,F403


def required_env(name: str) -> str:
    value = os.environ.get(name)  # noqa: F405
    if not value:
        raise RuntimeError(f"{name} must be set in the production environment")
    return value


# WhiteNoise serves collected static files from the app itself; prod has no
# separate web server in front of gunicorn. It must sit right after the
# security middleware.
MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")  # noqa: F405

DEBUG = False
SECRET_KEY = required_env("SECRET_KEY")
DATABASES["default"] = dj_database_url.parse(  # noqa: F405
    required_env("DATABASE_URL"),
    conn_max_age=600,
    conn_health_checks=True,
)

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")  # noqa: F405
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")  # noqa: F405

# Traefik terminates TLS and forwards the original scheme in this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}
