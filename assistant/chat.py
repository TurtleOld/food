"""Ход Ассистента: идемпотентное создание, выполнение в фоне и личная лента.

Ход выполняется воркером очереди, а не в HTTP-запросе: ответ модели с vision и
инструментами идёт десятки секунд. Повторная отправка того же запроса переиспользует
незавершённый ход, повторное выполнение задачи ничего не дублирует, а зависший ход
завершается понятной ошибкой, которую человек может повторить осознанно.
"""

import base64
import datetime
import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone

from assistant.budget import BudgetUnavailable
from assistant.gateway import LLMGatewayError, ToolCall, complete
from assistant.models import Conversation, Message, Turn
from assistant.photos import PreparedPhoto
from assistant.tools import TOOLS, dispatch_tool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Ты — Ассистент семейного трекера КБЖУ. Отвечай по-русски, кратко и по делу. "
    "Данные Дневника, Профиля и Каталога получай только инструментами и не выдумывай КБЖУ. "
    "Новые Продукты и Записи Дневника создавай только Предложением и сам ничего не подтверждай. "
    "Если человек не назвал дату или Приём пищи, спроси их до создания Предложения. "
    "По фото без этикетки количество в граммах или миллилитрах называет человек: не угадывай его."
)
PROVIDER_FAILED = "Ассистент не ответил: провайдер модели недоступен. Попробуйте ещё раз."
EMPTY_ANSWER = "Ассистент вернул пустой ответ. Попробуйте ещё раз."
TOO_MANY_STEPS = "Ассистент сделал слишком много шагов за один ход. Попробуйте ещё раз."
INTERRUPTED = "Ход прервался: прошло слишком много времени. Попробуйте ещё раз."
TURN_FAILED = "Не получилось выполнить ход. Попробуйте ещё раз."

ACTIVE_STATUSES = (Turn.Status.PENDING, Turn.Status.RUNNING)


class ChatError(Exception):
    """Ход нельзя создать или выполнить так, как просят."""


@dataclass(frozen=True)
class TurnStart:
    """Результат создания хода: сам ход и признак, что он только что создан."""

    turn: Turn
    created: bool


@dataclass(frozen=True)
class TurnCard:
    """Один ход для ленты: сообщение человека, ответ Ассистента и состояние."""

    turn: Turn
    user: Message | None
    answer: Message | None


def _dedupe_key(text: str, photo: PreparedPhoto | None) -> str:
    digest = hashlib.sha256(" ".join(text.split()).encode())
    if photo is not None:
        digest.update(b"\0")
        digest.update(photo.digest.encode())
    return digest.hexdigest()


def start_turn(
    member: Any,
    *,
    text: str,
    photo: PreparedPhoto | None = None,
) -> TurnStart:
    """Создаёт ход или возвращает уже ожидающий тот же запрос.

    Args:
        member: Член семьи, которому принадлежит личная лента.
        text: текст сообщения; может быть пустым, если есть фото.
        photo: проверенное фото из формы.

    Returns:
        Ход и признак, что он создан этим вызовом; при повторе `created` ложно.

    Raises:
        ChatError: нет ни текста, ни фото либо текст длиннее лимита.
    """
    clean = text.strip()
    if not clean and photo is None:
        raise ChatError("Введите сообщение или приложите фото")
    if len(clean) > settings.ASSISTANT_MESSAGE_MAX_CHARS:
        raise ChatError(f"Сообщение длиннее {settings.ASSISTANT_MESSAGE_MAX_CHARS} символов")
    key = _dedupe_key(clean, photo)
    conversation = Conversation.for_member(member)
    existing = _active_turn(conversation, key)
    if existing is not None:
        return TurnStart(existing, False)
    try:
        with transaction.atomic():
            turn = Turn.objects.create(
                conversation=conversation,
                request_text=clean,
                request_dedupe_key=key,
            )
            Message.objects.create(
                conversation=conversation,
                turn=turn,
                role=Message.Role.USER,
                text=clean,
                has_image=photo is not None,
                photo=photo.content if photo is not None else None,
            )
    except IntegrityError:
        # Параллельный POST успел создать ход первым: его и показываем.
        existing = _active_turn(conversation, key)
        if existing is None:
            raise
        return TurnStart(existing, False)
    return TurnStart(turn, True)


def _active_turn(conversation: Conversation, key: str) -> Turn | None:
    return Turn.objects.filter(
        conversation=conversation,
        request_dedupe_key=key,
        status__in=ACTIVE_STATUSES,
    ).first()


def _history(conversation: Conversation, turn: Turn) -> list[dict[str, Any]]:
    """Последние удачные сообщения в пределах лимитов; неудачные ходы в историю не идут."""
    messages = list(
        Message.objects.filter(
            conversation=conversation,
            turn__status=Turn.Status.SUCCEEDED,
        )
        .exclude(turn_id=turn.pk)
        .order_by("-created_at", "-pk")[: settings.ASSISTANT_HISTORY_MESSAGES]
    )
    budget = settings.ASSISTANT_HISTORY_CHARS
    selected: list[dict[str, Any]] = []
    for message in messages:
        if not message.text:
            continue
        if len(message.text) > budget:
            break
        budget -= len(message.text)
        selected.append({"role": message.role, "content": message.text})
    selected.reverse()
    return selected


def _user_content(message: Message) -> Any:
    """Текущее сообщение: текст и фото, причём фото бывает только у сообщения человека."""
    parts: list[dict[str, Any]] = []
    if message.text:
        parts.append({"type": "text", "text": message.text})
    if message.has_image and message.photo:
        data = bytes(message.photo)
        if data:
            encoded = base64.b64encode(data).decode()
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                }
            )
    if len(parts) == 1 and parts[0]["type"] == "text":
        return message.text
    return parts


def build_prompt(turn: Turn) -> list[dict[str, Any]]:
    """Собирает запрос модели: системная роль, ограниченная история и текущий ход."""
    message = turn.messages.filter(role=Message.Role.USER).first()
    if message is None:
        raise ChatError("У хода нет сообщения человека")
    prompt: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    prompt.extend(_history(turn.conversation, turn))
    prompt.append({"role": "user", "content": _user_content(message)})
    return prompt


def _tool_call_payload(calls: tuple[ToolCall, ...]) -> list[dict[str, Any]]:
    return [
        {
            "id": call.id,
            "type": "function",
            "function": {"name": call.name, "arguments": call.arguments},
        }
        for call in calls
    ]


def _add_cost(turn: Turn, cost: Decimal) -> None:
    Turn.objects.filter(pk=turn.pk).update(cost_rub=F("cost_rub") + cost)


def _run_agent(turn: Turn) -> str:
    """Ограниченный цикл «модель → инструменты → модель»; каждый вызов учитывается бюджетом."""
    member = turn.conversation.member
    prompt = build_prompt(turn)
    rounds = 0
    tool_calls = 0
    while True:
        result = complete(prompt, tools=TOOLS)
        _add_cost(turn, result.cost_rub)
        if result.tool_calls:
            if (
                rounds >= settings.ASSISTANT_MAX_TOOL_ROUNDS
                or tool_calls + len(result.tool_calls) > settings.ASSISTANT_MAX_TOOL_CALLS
            ):
                raise ChatError(TOO_MANY_STEPS)
            rounds += 1
            tool_calls += len(result.tool_calls)
            prompt.append(
                {
                    "role": "assistant",
                    "content": result.text or None,
                    "tool_calls": _tool_call_payload(result.tool_calls),
                }
            )
            for call in result.tool_calls:
                output = dispatch_tool(member, call.name, call.arguments)
                prompt.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(output, ensure_ascii=False),
                    }
                )
            continue
        text = result.text.strip()
        if not text:
            raise ChatError(EMPTY_ANSWER)
        return text


def _interrupt(turn: Turn, moment: datetime.datetime) -> None:
    """Завершает брошенный ход понятной ошибкой и освобождает фото."""
    turn.status = Turn.Status.FAILED
    turn.error_message = INTERRUPTED
    turn.finished_at = moment
    turn.save(update_fields=["status", "error_message", "finished_at"])
    _clear_photos(turn)


def _claim(turn_id: int) -> Turn | None:
    """Забирает ход в работу; повторная доставка задачи не выполняет его дважды."""
    now = timezone.now()
    stale_before = now - datetime.timedelta(minutes=settings.TASKS_STUCK_AFTER_MINUTES)
    with transaction.atomic():
        turn = (
            Turn.objects.select_for_update()
            .select_related("conversation")
            .filter(pk=turn_id)
            .first()
        )
        if turn is None:
            return None
        if turn.status == Turn.Status.PENDING:
            turn.status = Turn.Status.RUNNING
            turn.started_at = now
            turn.save(update_fields=["status", "started_at"])
            return turn
        if turn.status == Turn.Status.RUNNING and (
            turn.started_at is None or turn.started_at < stale_before
        ):
            # Воркер потерял задачу: стоимость уже могла быть потрачена, поэтому
            # повторно модель не зовём — человек повторит ход сам.
            _interrupt(turn, now)
        return None


def _finish(turn: Turn, *, status: str, text: str = "", error: str = "") -> None:
    with transaction.atomic():
        locked = Turn.objects.select_for_update().get(pk=turn.pk)
        if locked.status != Turn.Status.RUNNING:
            return
        if status == Turn.Status.SUCCEEDED:
            Message.objects.create(
                conversation=locked.conversation,
                turn=locked,
                role=Message.Role.ASSISTANT,
                text=text,
            )
        locked.status = status
        locked.error_message = error
        locked.finished_at = timezone.now()
        locked.save(update_fields=["status", "error_message", "finished_at"])


def _clear_photos(turn: Turn) -> None:
    """Фото нужно только на время хода; после ответа или ошибки его в БД не остаётся."""
    turn.messages.exclude(photo=None).update(photo=None)


def execute_turn(turn_id: int) -> None:
    """Выполняет ход один раз; повторный вызов для того же хода безопасен.

    Ошибка бюджета или провайдера завершает ход понятным русским сообщением,
    сохранённые данные не меняются, а фото удаляется в любом исходе.
    """
    turn = _claim(turn_id)
    if turn is None:
        return
    try:
        try:
            text = _run_agent(turn)
        except BudgetUnavailable as error:
            _finish(turn, status=Turn.Status.FAILED, error=str(error))
        except LLMGatewayError:
            logger.warning("Assistant turn %s: provider failed", turn_id)
            _finish(turn, status=Turn.Status.FAILED, error=PROVIDER_FAILED)
        except ChatError as error:
            _finish(turn, status=Turn.Status.FAILED, error=str(error))
        else:
            _finish(turn, status=Turn.Status.SUCCEEDED, text=text)
    except Exception:
        logger.exception("Assistant turn %s failed unexpectedly", turn_id)
        _finish(turn, status=Turn.Status.FAILED, error=TURN_FAILED)
    finally:
        _clear_photos(turn)


def recover_stale_running_turns(
    conversation: Conversation, now: datetime.datetime | None = None
) -> int:
    """Завершает ход, чей воркер пропал: иначе лента ждала бы его вечно.

    Задача могла быть уже доставлена повторно и завершиться, пока первый воркер
    завис. Такой ход больше никто не разбудит, поэтому его завершает просмотр ленты.
    """
    moment = now or timezone.now()
    cutoff = moment - datetime.timedelta(minutes=settings.TASKS_STUCK_AFTER_MINUTES)
    recovered = 0
    with transaction.atomic():
        turns = Turn.objects.select_for_update().filter(
            conversation=conversation,
            status=Turn.Status.RUNNING,
        )
        turns = turns.filter(Q(started_at__isnull=True) | Q(started_at__lt=cutoff))
        for turn in turns:
            _interrupt(turn, moment)
            recovered += 1
    return recovered


def recover_stale_pending_turns(
    conversation: Conversation, now: datetime.datetime | None = None
) -> list[int]:
    """Возвращает в очередь ходы, чья задача потерялась между коммитом и постановкой.

    Повторный опрос ленты не должен плодить задачи, поэтому ход отмечается
    `recovery_at` и пропускается до следующего окна восстановления.
    """
    moment = now or timezone.now()
    cutoff = moment - datetime.timedelta(minutes=settings.TASKS_STUCK_AFTER_MINUTES)
    with transaction.atomic():
        turns = Turn.objects.select_for_update().filter(
            conversation=conversation,
            status=Turn.Status.PENDING,
            created_at__lt=cutoff,
        )
        turns = turns.filter(Q(recovery_at__isnull=True) | Q(recovery_at__lt=cutoff))
        turn_ids = []
        for turn in turns:
            turn.recovery_at = moment
            turn.save(update_fields=["recovery_at"])
            turn_ids.append(turn.pk)
    return turn_ids


def turn_cards(conversation: Conversation) -> list[TurnCard]:
    """Последние ходы ленты по возрастанию времени, с их сообщениями."""
    turns = list(
        Turn.objects.filter(conversation=conversation)
        # Байты фото нужны только воркеру; лента проверяет лишь `has_image`.
        .prefetch_related(Prefetch("messages", queryset=Message.objects.defer("photo")))
        .order_by("-created_at", "-pk")[: settings.ASSISTANT_FEED_TURNS]
    )
    turns.reverse()
    cards = []
    for turn in turns:
        messages = list(turn.messages.all())
        cards.append(
            TurnCard(
                turn=turn,
                user=next((m for m in messages if m.role == Message.Role.USER), None),
                answer=next((m for m in messages if m.role == Message.Role.ASSISTANT), None),
            )
        )
    return cards
