import json
import re

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import StaticFilesStorage
from django.test import TestCase, override_settings
from django.urls import reverse

User = get_user_model()


class SuffixedStaticStorage(StaticFilesStorage):
    """Имитирует хэшированные URL продовой статики без collectstatic."""

    suffix = "abc123"

    def url(self, name):
        stem, dot, ext = name.rpartition(".")
        return f"/static/{stem}.{self.suffix}.{ext}"


class OtherSuffixStaticStorage(SuffixedStaticStorage):
    suffix = "def456"


def hashed_storage(storage_class):
    return override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": f"{__name__}.{storage_class.__name__}"},
        }
    )


class ServiceWorkerTest(TestCase):
    """The service worker must be reachable from the origin root, unauthenticated."""

    def test_service_worker_is_served_from_root_with_full_scope(self):
        response = self.client.get(reverse("core:service_worker"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/javascript")
        self.assertEqual(response["Service-Worker-Allowed"], "/")

    def test_service_worker_is_public(self):
        member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(member)

        response = self.client.get(reverse("core:service_worker"))

        self.assertEqual(response.status_code, 200)


class ManifestTest(TestCase):
    """The web app manifest drives installability."""

    def test_day_page_links_the_manifest(self):
        member = User.objects.create_user(username="alice", password="s3cret-pass")
        self.client.force_login(member)

        response = self.client.get(reverse("core:day"))

        self.assertContains(response, "manifest.webmanifest")

    def test_manifest_file_exists_in_static_sources(self):
        self.assertIsNotNone(finders.find("manifest.webmanifest"))


class ServiceWorkerContentTest(TestCase):
    def cache_name(self):
        match = re.search(r"food-static-[0-9a-f]+", self.fetch())
        assert match
        return match.group(0)

    def fetch(self):
        return self.client.get(reverse("core:service_worker")).content.decode()

    @hashed_storage(SuffixedStaticStorage)
    def test_precaches_hashed_static_urls(self):
        body = self.fetch()

        for name in (
            "vendor/htmx/htmx-2.0.11.min",
            "vendor/alpine/alpine-3.17.4.min",
            "vendor/bulma/bulma-1.0.4.min",
            "css/app",
            "manifest",
            "icons/icon-192",
            "offline",
        ):
            self.assertIn(f"/static/{name}.abc123.", body)

    @hashed_storage(SuffixedStaticStorage)
    def test_cache_name_is_derived_from_precache_list(self):
        first = self.cache_name()

        with hashed_storage(OtherSuffixStaticStorage):
            second = self.cache_name()

        self.assertNotEqual(first, second)

    @hashed_storage(SuffixedStaticStorage)
    def test_cache_name_is_stable_for_same_list(self):
        self.assertEqual(self.fetch(), self.fetch())

    @hashed_storage(SuffixedStaticStorage)
    def test_precache_excludes_scanner_polyfill_and_html_routes(self):
        body = self.fetch()
        match = re.search(r"PRECACHE_URLS = (\[.*?\]);", body, re.S)
        assert match

        urls = json.loads(match.group(1))

        self.assertTrue(urls)
        self.assertTrue(all(url.startswith("/static/") for url in urls))
        self.assertFalse(any("barcode" in url or "wasm" in url for url in urls))

    def test_debug_flag_is_exposed_to_worker(self):
        with override_settings(DEBUG=True):
            self.assertIn("const BYPASS_CACHE = true;", self.fetch())
        with override_settings(DEBUG=False):
            self.assertIn("const BYPASS_CACHE = false;", self.fetch())


class PwaIdentityTest(TestCase):
    def manifest(self):
        path = finders.find("manifest.webmanifest")
        assert isinstance(path, str)
        with open(path, encoding="utf-8") as source:
            return json.load(source)

    def test_manifest_fields(self):
        manifest = self.manifest()

        self.assertEqual(manifest["display"], "standalone")
        self.assertNotIn("orientation", manifest)
        self.assertEqual(manifest["theme_color"], "#f7f9fa")
        self.assertEqual(manifest["background_color"], "#f7f9fa")

    def test_manifest_declares_any_and_maskable_icons(self):
        icons = {(icon["sizes"], icon["purpose"]) for icon in self.manifest()["icons"]}

        self.assertEqual(icons, {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")})

    def test_icon_files_exist(self):
        for icon in self.manifest()["icons"]:
            path = icon["src"].removeprefix("/static/")
            self.assertIsNotNone(finders.find(path), path)
        self.assertIsNotNone(finders.find("icons/apple-touch-icon.png"))
        self.assertIsNotNone(finders.find("icons/favicon.svg"))

    def test_head_has_light_and_dark_theme_colors(self):
        response = self.client.get(reverse("core:login"))

        light = 'name="theme-color" content="#f7f9fa" media="(prefers-color-scheme: light)"'
        dark = 'name="theme-color" content="#16181d" media="(prefers-color-scheme: dark)"'
        self.assertContains(response, light)
        self.assertContains(response, dark)
