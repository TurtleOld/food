from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpResponse
from django.urls import reverse, reverse_lazy
from django.views.generic import TemplateView, UpdateView

from assistant.budget import month_summary
from assistant.forms import LLMSettingsForm
from assistant.models import LLMSettings


def format_rub(value: Decimal) -> str:
    """Рубли для экрана: неразрывные пробелы между разрядами и запятая перед копейками."""
    return f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")


class BudgetView(LoginRequiredMixin, TemplateView):
    """Расход и остаток общего бюджета; настройки доступны только администратору."""

    template_name = "assistant/budget.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        summary = month_summary()
        context.update(
            {
                "summary": summary,
                "budget": format_rub(summary.budget),
                "spent": format_rub(summary.spent),
                "reserved": format_rub(summary.reserved),
                "remaining": format_rub(summary.remaining),
                "can_edit": self.request.user.is_staff,
            }
        )
        return context


class SettingsView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    """Настройки провайдера, бюджета и тарифов; меняет только администратор семьи."""

    form_class = LLMSettingsForm
    template_name = "assistant/settings.html"
    success_url = reverse_lazy("assistant:budget")
    raise_exception = True

    def test_func(self) -> bool:
        return bool(self.request.user.is_staff)

    def get_object(self, queryset: QuerySet[LLMSettings] | None = None) -> LLMSettings:
        return LLMSettings.current()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["masked_key"] = self.object.masked_api_key if self.object.pk else ""
        context["cancel_url"] = reverse("assistant:budget")
        return context

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        messages.success(self.request, "Настройки сохранены")
        return super().form_valid(form)
