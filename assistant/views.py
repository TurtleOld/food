from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.generic import ListView, TemplateView, UpdateView, View

from assistant.budget import month_summary
from assistant.chat import (
    ChatError,
    cancel_active_turn,
    recover_stale_pending_turns,
    recover_stale_running_turns,
    retry_turn,
    start_turn,
    turn_page,
)
from assistant.forms import (
    ChatMessageForm,
    LLMSettingsForm,
    ProposalForm,
    SummarySettingsForm,
)
from assistant.models import Conversation, LLMSettings, Proposal, SummarySettings
from assistant.proposals import (
    ProposalError,
    ProposalNotFound,
    confirm_proposal,
    reject_proposal,
    update_pending_proposal,
)
from assistant.tasks import execute_turn_task
from core.htmx import FragmentMixin, HtmxLoginRequiredMixin, sheet_saved_response, with_toasts
from core.jobs import enqueue_on_commit
from core.models import Product


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


class SummarySettingsView(LoginRequiredMixin, UpdateView):
    """Расписание Сводок текущего Члена семьи; каждый меняет только своё."""

    form_class = SummarySettingsForm
    template_name = "assistant/summaries.html"
    success_url = reverse_lazy("assistant:summaries")

    def get_object(self, queryset: QuerySet[SummarySettings] | None = None) -> SummarySettings:
        return SummarySettings.for_member(self.request.user)

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        messages.success(self.request, "Расписание сводок сохранено")
        return super().form_valid(form)


class ProposalListView(LoginRequiredMixin, ListView):
    """Предложения текущего Члена семьи: ожидающие решения — первыми."""

    template_name = "assistant/proposals.html"
    context_object_name = "proposals"

    def get_queryset(self) -> QuerySet[Proposal]:
        return Proposal.objects.filter(member_id=self.request.user.pk).select_related("product")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        pending: list[Proposal] = []
        decided: list[Proposal] = []
        for proposal in self.object_list:
            if proposal.status == Proposal.Status.PENDING:
                pending.append(proposal)
            else:
                decided.append(proposal)
        context.update(pending=pending, decided=decided)
        return context


class ProposalUpdateView(LoginRequiredMixin, UpdateView):
    """Правит ожидающее Предложение; чужие и уже решённые недоступны."""

    model = Proposal
    form_class = ProposalForm
    template_name = "assistant/proposal_form.html"
    context_object_name = "proposal"

    def get_queryset(self) -> QuerySet[Proposal]:
        return Proposal.objects.filter(
            member_id=self.request.user.pk, status=Proposal.Status.PENDING
        )

    def get_template_names(self) -> list[str]:
        """Из чата правка открывается шторкой, с отдельного адреса — обычной страницей."""
        if self.request.htmx:  # type: ignore[attr-defined]
            return ["assistant/proposal_form.html#sheet"]
        return super().get_template_names()

    def get_initial(self) -> dict[str, Any]:
        initial = super().get_initial()
        product_id = self.request.GET.get("product")
        if product_id and self.object.kind == Proposal.Kind.ENTRY:
            try:
                product = Product.objects.filter(pk=product_id).first()
            except (TypeError, ValueError):
                product = None
            if product is not None:
                initial["product"] = product
        return initial

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["cancel_url"] = reverse("assistant:proposals")
        return context

    def form_invalid(self, form: ModelForm[Any]) -> HttpResponse:
        """Ошибки правки в шторке остаются в шторке, а не заменяют собой всю ленту."""
        if self.request.htmx:  # type: ignore[attr-defined]
            return self.render_to_response(self.get_context_data(form=form))
        return super().form_invalid(form)

    def form_valid(self, form: ModelForm[Any]) -> HttpResponse:
        try:
            update_pending_proposal(self.request.user, self.object.pk, form.cleaned_data)
        except ProposalNotFound as error:
            messages.error(self.request, str(error))
        else:
            messages.success(self.request, "Предложение поправлено")
        if self.request.htmx:  # type: ignore[attr-defined]
            return sheet_saved_response(
                self.request, "assistant/chat.html#feed", _chat_context(self.request.user), "#feed"
            )
        return redirect("assistant:proposals")


class ProposalDecisionView(LoginRequiredMixin, View):
    """Общее для подтверждения и отклонения: только POST и возврат к карточкам."""

    http_method_names = ["post"]
    success_message = ""

    def decide(self, member: Any, proposal_id: int) -> Proposal:
        raise NotImplementedError

    def post(self, request: HttpRequest, pk: int) -> HttpResponse:
        proposal = get_object_or_404(Proposal, pk=pk, member=request.user)
        try:
            self.decide(request.user, proposal.pk)
        except ProposalError as error:
            messages.error(request, str(error))
        else:
            messages.success(request, self.success_message)
        if request.htmx:  # type: ignore[attr-defined]
            # Решение из карточки в чате обновляет ленту на месте, без ухода со страницы.
            response = render(request, "assistant/chat.html#feed", _chat_context(request.user))
            return with_toasts(response, request)
        return redirect("assistant:proposals")


class ProposalConfirmView(ProposalDecisionView):
    """Подтверждает Предложение; повтор не создаёт вторую Запись."""

    success_message = "Предложение подтверждено"

    def decide(self, member: Any, proposal_id: int) -> Proposal:
        return confirm_proposal(member, proposal_id)


class ProposalRejectView(ProposalDecisionView):
    """Отклоняет Предложение; данные Дневника не создаются."""

    success_message = "Предложение отклонено"

    def decide(self, member: Any, proposal_id: int) -> Proposal:
        return reject_proposal(member, proposal_id)


def _chat_context(
    member: Any,
    *,
    window: tuple[datetime.datetime, int] | None = None,
    load_older: bool = False,
    form: ChatMessageForm | None = None,
) -> dict[str, Any]:
    """Лента человека и форма отправки; потерянные задачи ходов возвращаются в очередь.

    Адреса опроса и подгрузки несут курсор показанного окна: иначе ответ опроса
    или порция ранних ходов откатили бы ленту к первой порции.
    """
    conversation = Conversation.for_member(member)
    recover_stale_running_turns(conversation)
    for turn_id in recover_stale_pending_turns(conversation):
        enqueue_on_commit(execute_turn_task, turn_id)
    page = turn_page(conversation, since=window, load_older=load_older)
    poll_url = older_url = ""
    if page.oldest is not None:
        cursor = {
            "since": page.oldest.created_at.isoformat(),
            "since_pk": page.oldest.pk,
        }
        poll_url = f"{reverse('assistant:chat')}?{urlencode(cursor)}"
        if page.has_earlier:
            older_url = f"{poll_url}&older=1"
    return {
        "cards": page.cards,
        "active": any(card.turn.is_active for card in page.cards),
        "poll_url": poll_url,
        "older_url": older_url,
        "pending_proposals": Proposal.objects.filter(
            member_id=member.pk, status=Proposal.Status.PENDING
        ).select_related("product"),
        "form": form or ChatMessageForm(),
        "photo_max_mb": max(1, settings.ASSISTANT_PHOTO_MAX_BYTES // (1024 * 1024)),
        "can_edit": member.is_staff,
    }


def _window_start(request: HttpRequest) -> tuple[datetime.datetime, int] | None:
    """Начало показанного окна из `since`/`since_pk`; испорченный адрес окно не ломает."""
    raw, raw_pk = request.GET.get("since"), request.GET.get("since_pk")
    if not raw or not raw_pk:
        return None
    moment = parse_datetime(raw)
    try:
        pk = int(raw_pk)
    except (TypeError, ValueError):
        return None
    if moment is None or timezone.is_naive(moment) or pk < 1:
        return None
    return moment, pk


class ChatView(HtmxLoginRequiredMixin, FragmentMixin, TemplateView):
    """Личная лента Ассистента; htmx опрашивает фрагмент `feed`, пока ход не готов."""

    template_name = "assistant/chat.html"
    fragment_template_name = "assistant/chat.html#feed"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(
            _chat_context(
                self.request.user,
                window=_window_start(self.request),
                load_older=self.request.GET.get("older") == "1",
            )
        )
        return context


def _chat_response(request: HttpRequest, form: ChatMessageForm | None = None) -> HttpResponse:
    """Полная страница ленты или её фрагмент `chat` с лентой и формой для htmx."""
    template = "assistant/chat.html#chat" if request.htmx else "assistant/chat.html"  # type: ignore[attr-defined]
    return render(request, template, _chat_context(request.user, form=form))


class ChatSendView(HtmxLoginRequiredMixin, View):
    """Создаёт ход и ставит его задачу в очередь после коммита; повтор не даёт дубль."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        form = ChatMessageForm(request.POST, request.FILES)
        if not form.is_valid():
            return _chat_response(request, form)
        try:
            retry_turn_id = form.cleaned_data.get("retry_turn_id")
            if retry_turn_id is not None:
                started = retry_turn(request.user, retry_turn_id)
            else:
                started = start_turn(
                    request.user,
                    text=form.cleaned_data["text"],
                    photo=form.cleaned_data.get("photo"),
                )
        except ChatError as error:
            form.add_error(None, str(error))
            return _chat_response(request, form)
        if started.created:
            enqueue_on_commit(execute_turn_task, started.turn.pk)
        if request.htmx:  # type: ignore[attr-defined]
            return _chat_response(request)
        return redirect("assistant:chat")


class ChatCancelView(HtmxLoginRequiredMixin, View):
    """Отменяет зависший ход: застрял в очереди или воркер пропал."""

    http_method_names = ["post"]

    def post(self, request: HttpRequest) -> HttpResponse:
        cancel_active_turn(Conversation.for_member(request.user))
        if request.htmx:  # type: ignore[attr-defined]
            return _chat_response(request)
        return redirect("assistant:chat")
