from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


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
