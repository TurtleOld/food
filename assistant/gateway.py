"""OpenAI-совместимый шлюз: текст, vision и tools через один вызов чата.

Логика приложения зависит от этого интерфейса, а не от конкретной модели: base URL,
имя модели и ключ приходят из БД. Перед запросом бюджет резервируется, после ответа
фактический usage записывается по тарифам момента запроса; ошибка возвращает резерв.
"""

import json
import logging
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.request import Request, urlopen

from assistant.budget import BudgetUnavailable, release, reserve, settle
from assistant.models import LLMSettings
from assistant.prompt_budget import estimated_input_tokens

logger = logging.getLogger(__name__)


class LLMGatewayError(Exception):
    """Провайдер не ответил или ответил так, что стоимость нельзя учесть."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class LLMResult:
    text: str
    tool_calls: tuple[ToolCall, ...]
    usage: Usage
    cost_rub: Decimal
    model: str


def _payload(
    settings: LLMSettings,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": settings.model,
        "messages": list(messages),
        "max_tokens": settings.max_output_tokens,
    }
    if tools:
        payload["tools"] = list(tools)
    return payload


def _post(settings: LLMSettings, payload: dict[str, Any]) -> dict[str, Any]:
    logger.info("LLM -> %s, model %s", settings.base_url, settings.model)
    logger.debug("LLM payload: %s", json.dumps(payload, ensure_ascii=False))
    request = Request(
        f"{settings.base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key}",
        },
        method="POST",
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=settings.timeout_seconds) as response:  # noqa: S310
            body = response.read()
    except (OSError, ValueError) as error:  # URLError, HTTPError и таймаут — тоже OSError
        latency = time.monotonic() - started
        logger.warning("LLM <- %s after %.1fs: %s", settings.base_url, latency, error)
        raise LLMGatewayError("Провайдер модели не ответил") from error
    logger.info("LLM <- %s bytes in %.1fs", len(body), time.monotonic() - started)
    try:
        data = json.loads(body)
    except ValueError as error:
        raise LLMGatewayError("Провайдер модели вернул неверный ответ") from error
    if not isinstance(data, dict):
        raise LLMGatewayError("Провайдер модели вернул неверный ответ")
    return data


def _token_count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LLMGatewayError("Провайдер модели не сообщил usage")
    return value


def _parse(data: dict[str, Any]) -> tuple[str, tuple[ToolCall, ...], Usage]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LLMGatewayError("Провайдер модели вернул неверный ответ")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise LLMGatewayError("Провайдер модели вернул неверный ответ")
    content = message.get("content")
    text = content if isinstance(content, str) else ""

    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        raise LLMGatewayError("Провайдер модели вернул неверный ответ")
    tool_calls = []
    for call in raw_calls:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise LLMGatewayError("Провайдер модели вернул неверный ответ")
        tool_calls.append(
            ToolCall(
                id=str(call.get("id") or ""),
                name=function["name"],
                arguments=str(function.get("arguments") or ""),
            )
        )

    usage = data.get("usage")
    if not isinstance(usage, dict):
        raise LLMGatewayError("Провайдер модели не сообщил usage")
    details = usage.get("prompt_tokens_details")
    cached = 0
    if isinstance(details, dict) and "cached_tokens" in details:
        cached = _token_count(details["cached_tokens"])
    input_tokens = _token_count(usage.get("prompt_tokens"))
    return (
        text,
        tuple(tool_calls),
        Usage(
            input_tokens=input_tokens,
            cached_input_tokens=min(cached, input_tokens),
            output_tokens=_token_count(usage.get("completion_tokens")),
        ),
    )


def complete(
    messages: Sequence[Mapping[str, Any]],
    *,
    tools: Sequence[Mapping[str, Any]] | None = None,
) -> LLMResult:
    """Один вызов модели; текст, изображения и tools передаются как есть.

    Raises:
        BudgetUnavailable: ассистент не настроен, бюджет исчерпан или вход больше лимита.
        LLMGatewayError: провайдер не ответил либо ответ нельзя учесть; резерв возвращён.
    """
    settings = LLMSettings.current()
    if not settings.is_configured:
        raise BudgetUnavailable(
            "Ассистент не настроен: администратор должен указать провайдера, тарифы и бюджет."
        )
    if estimated_input_tokens(messages, tools) > settings.max_input_tokens:
        raise BudgetUnavailable(
            "Запрос слишком большой для настроенного лимита. Сократите текст или фото."
        )

    reservation = reserve(settings, request_id=uuid.uuid4().hex)
    try:
        data = _post(settings, _payload(settings, messages, tools))
        text, tool_calls, usage = _parse(data)
        record = settle(
            reservation,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
        )
    except Exception:
        release(reservation)
        raise
    return LLMResult(
        text=text,
        tool_calls=tool_calls,
        usage=usage,
        cost_rub=record.cost_rub,
        model=record.model,
    )
