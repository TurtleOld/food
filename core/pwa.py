"""PWA: что service worker кладёт в кэш заранее и как по этому выводится имя кэша."""

import hashlib

from django.templatetags.static import static

# Сканерный полифилл и wasm тяжёлые и грузятся лениво (static/js/scanner.js), поэтому не здесь.
PRECACHE_STATIC_FILES = (
    "vendor/htmx/htmx-2.0.11.min.js",
    "vendor/alpine/alpine-3.17.4.min.js",
    "vendor/bulma/bulma-1.0.4.min.css",
    "css/app.css",
    "js/sheet.js",
    "js/scanner.js",
    "manifest.webmanifest",
    "icons/icon-192.png",
    "icons/icon-512.png",
    "icons/icon-maskable-512.png",
    "icons/apple-touch-icon.png",
    "icons/favicon.svg",
    "offline.html",
)


def precache_urls() -> list[str]:
    """URL статики для предзагрузки; у хэшированного хранилища они содержат хэш содержимого."""
    return [static(name) for name in PRECACHE_STATIC_FILES]


def cache_name(urls: list[str]) -> str:
    """Имя кэша, выведенное из списка `urls`: новая статика даёт новое имя и свежий воркер."""
    digest = hashlib.sha256("\n".join(urls).encode()).hexdigest()[:12]
    return f"food-static-{digest}"
