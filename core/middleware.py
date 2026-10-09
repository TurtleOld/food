from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_vary_headers


class VaryHtmxRequestMiddleware:
    """Помечает ответы `Vary: HX-Request`: один URL отдаёт страницу или фрагмент."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        patch_vary_headers(response, ["HX-Request"])
        return response
