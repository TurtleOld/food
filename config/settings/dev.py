"""Development settings: local Compose with its own Postgres."""

from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]
CSRF_TRUSTED_ORIGINS = env_list(  # noqa: F405
    "CSRF_TRUSTED_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000",
)
