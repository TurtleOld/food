"""Консервативная оценка размера запроса модели и подгонка результатов инструментов.

Без токенизатора: один токен на байт UTF-8 — верхняя граница для BPE, безопасная
для кириллицы. Фото оценивается фиксированным бюджетом на высокое разрешение, а
схемы инструментов — своим JSON. Оценка позволяет собирать запрос под фактический
лимит провайдера, а не обрезать историю по числу символов.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

IMAGE_TOKENS = 4096
MESSAGE_OVERHEAD_TOKENS = 16
# Потолок одного результата инструмента и запас на такие результаты при отборе
# истории: поздние раунды «модель → инструменты» не должны выходить за лимит.
TOOL_RESULT_MAX_TOKENS = 8192
TOOL_CONTEXT_RESERVE_TOKENS = 1024
TOOL_RESULT_TRUNCATED = '{"truncated": "Результат инструмента сокращён, чтобы уложиться в лимит."}'


def text_tokens(text: str) -> int:
    """Верхняя оценка токенов строки: её размер в UTF-8."""
    return len(text.encode("utf-8"))


def content_tokens(content: Any) -> int:
    """Оценка содержимого сообщения: строки, частей и фото."""
    if isinstance(content, str):
        return text_tokens(content)
    if not isinstance(content, list):
        return 0
    tokens = 0
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image_url":
            tokens += IMAGE_TOKENS
        elif isinstance(part.get("text"), str):
            tokens += text_tokens(part["text"])
    return tokens


def message_tokens(message: Mapping[str, Any]) -> int:
    """Оценка одного сообщения вместе с вызовами инструментов и накладными расходами."""
    tokens = content_tokens(message.get("content")) + MESSAGE_OVERHEAD_TOKENS
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for call in tool_calls:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict):
                tokens += text_tokens(str(function.get("name") or ""))
                tokens += text_tokens(str(function.get("arguments") or ""))
    return tokens


def tools_tokens(tools: Sequence[Mapping[str, Any]] | None) -> int:
    """Оценка схем инструментов, которые уходят провайдеру вместе с сообщениями."""
    if not tools:
        return 0
    return text_tokens(json.dumps(tools, ensure_ascii=False))


def estimated_input_tokens(
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None = None,
) -> int:
    """Консервативная оценка всего входа: сообщения, tool_calls, схемы tools и фото."""
    return sum(message_tokens(message) for message in messages) + tools_tokens(tools)


def user_message_tokens(text: str, has_photo: bool) -> int:
    """Оценка текущего сообщения человека с необязательным фото."""
    return text_tokens(text) + (IMAGE_TOKENS if has_photo else 0) + MESSAGE_OVERHEAD_TOKENS


def truncate_text(text: str, max_tokens: int) -> str:
    """Обрезает текст по оценке токенов, не разрывая многобайтовый символ."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_tokens:
        return text
    if max_tokens <= 0:
        return ""
    marker_size = text_tokens(TOOL_RESULT_TRUNCATED)
    if max_tokens <= marker_size:
        return encoded[:max_tokens].decode("utf-8", errors="ignore")
    head = encoded[: max_tokens - marker_size].decode("utf-8", errors="ignore")
    return head + TOOL_RESULT_TRUNCATED


def tool_result_content(output: Any) -> str:
    """JSON результата инструмента, заранее ограниченный по размеру."""
    return truncate_text(json.dumps(output, ensure_ascii=False), TOOL_RESULT_MAX_TOKENS)


def _drop_oldest_tool_exchange(messages: list[dict[str, Any]]) -> bool:
    """Удаляет старейший блок «вызов инструментов → их результаты» целиком."""
    for index, message in enumerate(messages):
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        end = index + 1
        while end < len(messages) and messages[end].get("role") == "tool":
            end += 1
        del messages[index:end]
        return True
    return False


def fit_tool_results(
    messages: list[dict[str, Any]],
    max_input_tokens: int,
    *,
    tools: Sequence[Mapping[str, Any]] | None = None,
) -> None:
    """Сокращает результаты инструментов, чтобы вход уложился в лимит провайдера.

    Новые результаты важнее старых, поэтому бюджет раздаётся от новых к старым:
    при нехватке места старые результаты сокращаются первыми. Системная роль,
    история и текущее сообщение не меняются. Если даже пустые результаты не
    помещаются, старейший блок вызова инструментов удаляется целиком.
    """
    indices = [index for index, message in enumerate(messages) if message.get("role") == "tool"]
    if not indices:
        return
    fixed = tools_tokens(tools) + sum(
        message_tokens(message) for index, message in enumerate(messages) if index not in indices
    )
    available = max_input_tokens - fixed - MESSAGE_OVERHEAD_TOKENS * len(indices)
    for index in reversed(indices):
        content = str(messages[index].get("content") or "")
        content = truncate_text(content, max(0, available))
        messages[index]["content"] = content
        available -= text_tokens(content)
    if estimated_input_tokens(messages, tools) > max_input_tokens:
        if _drop_oldest_tool_exchange(messages):
            fit_tool_results(messages, max_input_tokens, tools=tools)
