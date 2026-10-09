from django.test import SimpleTestCase
from django.urls import reverse


class VaryHtmxRequestTests(SimpleTestCase):
    def test_every_response_varies_on_hx_request(self) -> None:
        response = self.client.get(reverse("core:healthz"))

        self.assertIn("HX-Request", response["Vary"])

    def test_htmx_response_varies_on_hx_request(self) -> None:
        response = self.client.get(reverse("core:healthz"), headers={"HX-Request": "true"})

        self.assertIn("HX-Request", response["Vary"])
