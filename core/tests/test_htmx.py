from django.http import HttpRequest, HttpResponse
from django.test import SimpleTestCase, override_settings
from django.urls import path
from django.views import View


class EchoHtmx(View):
    def get(self, request: HttpRequest) -> HttpResponse:
        return HttpResponse("htmx" if request.htmx else "plain")  # type: ignore[attr-defined]


urlpatterns = [path("echo/", EchoHtmx.as_view())]


@override_settings(ROOT_URLCONF=__name__)
class HtmxRequestFlagTests(SimpleTestCase):
    def test_request_with_hx_header_is_htmx(self) -> None:
        response = self.client.get("/echo/", headers={"HX-Request": "true"})

        self.assertEqual(response.content, b"htmx")

    def test_plain_request_is_not_htmx(self) -> None:
        response = self.client.get("/echo/")

        self.assertEqual(response.content, b"plain")
