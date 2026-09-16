from django.test import TestCase


class HomePageTest(TestCase):
    """First sample test: exercises the single testing seam over HTTP."""

    def test_home_page_is_available(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "КБЖУ")

    def test_health_endpoint_is_available(self):
        response = self.client.get("/healthz")

        self.assertEqual(response.status_code, 200)
