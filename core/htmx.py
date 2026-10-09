"""Общая инфраструктура фрагментов htmx: страница или фрагмент по одному URL."""

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django_htmx.http import HttpResponseClientRedirect, trigger_client_event


class HtmxLoginRequiredMixin(LoginRequiredMixin):
    """Требует вход; на htmx-запросе без сессии отвечает `HX-Redirect`, иначе 302.

    Обычный 302 htmx подставил бы страницу входа в target фрагмента.
    """

    request: HttpRequest

    def handle_no_permission(self) -> HttpResponse:  # type: ignore[override]
        if not self.request.htmx:  # type: ignore[attr-defined]
            return super().handle_no_permission()
        next_url = self.request.htmx.current_url_abs_path or self.request.get_full_path()  # type: ignore[attr-defined]
        login = redirect_to_login(next_url, self.get_login_url(), self.get_redirect_field_name())
        return HttpResponseClientRedirect(login.url)


class FragmentMixin:
    """CBV-миксин: при htmx-запросе рендерит `fragment_template_name`, иначе страницу.

    Фрагмент — `{% partialdef имя inline %}` страницы-владельца (`core/day.html#feed`);
    `id` target в DOM совпадает с именем partial.
    """

    request: HttpRequest
    fragment_template_name: str | None = None

    def get_template_names(self) -> list[str]:
        if self.request.htmx and self.fragment_template_name:  # type: ignore[attr-defined]
            return [self.fragment_template_name]
        return super().get_template_names()  # type: ignore[misc]


def with_toasts(response: HttpResponse, request: HttpRequest) -> HttpResponse:
    """Дописывает к htmx-ответу OOB-тосты из Django `messages`."""
    response.content += render_to_string("components/toasts_oob.html", request=request).encode()
    return response


def sheet_saved_response(
    request: HttpRequest, template: str, context: dict[str, Any], target: str
) -> HttpResponse:
    """Ответ на успешный htmx-POST из шторки: фрагмент в `target`, тост и закрытие шторки."""
    response = HttpResponse(render_to_string(template, context, request=request))
    with_toasts(response, request)
    response["HX-Retarget"] = target
    response["HX-Reswap"] = "outerHTML"
    trigger_client_event(response, "sheet:close")
    return response
