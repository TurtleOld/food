"""Безопасное отображение Markdown только в ответах Ассистента."""

import re
from collections.abc import MutableMapping, Sequence
from typing import Any

from django import template
from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

register = template.Library()

# Числовые ячейки КБЖУ: сравнения («≥643»), десятичные запятые и единицы измерения.
_NUMERIC_CELL = re.compile(
    r"^[≥≤<>~≈]?\s*[+-]?\d+(?:[ \u00a0]\d{3})*(?:[.,]\d+)?\s*(?:%|ккал|мг|г|мл|кг|л|шт)?$",
    re.IGNORECASE,
)
_PLACEHOLDER_CELLS = {"", "—", "–", "-"}
_TOTAL_ROW_PREFIX = "итого"


def _cell_text(tokens: Sequence[Token], index: int) -> str:
    if index + 1 >= len(tokens):
        return ""
    inline = tokens[index + 1]
    return inline.content.strip() if inline.type == "inline" else ""


def _numeric_columns(rows: list[list[tuple[int, str]]]) -> set[int]:
    """Возвращает номера колонок, где все значения — числа или количества."""
    numeric: set[int] = set()
    for column in range(max((len(cells) for cells in rows), default=0)):
        values = [cells[column][1] for cells in rows if column < len(cells)]
        values = [value for value in values if value not in _PLACEHOLDER_CELLS]
        if values and all(_NUMERIC_CELL.match(value) for value in values):
            numeric.add(column)
    return numeric


def _decorate_table(tokens: Sequence[Token], start: int, end: int) -> None:
    in_head = False
    header_rows: list[list[tuple[int, str]]] = []
    body_rows: list[list[tuple[int, str]]] = []
    body_positions: list[int] = []
    for index in range(start, end):
        token = tokens[index]
        if token.type == "thead_open":
            in_head = True
        elif token.type == "thead_close":
            in_head = False
        elif token.type == "tr_open":
            cells: list[tuple[int, str]] = []
            cursor = index + 1
            while cursor < end and tokens[cursor].type != "tr_close":
                if tokens[cursor].type in {"th_open", "td_open"}:
                    cells.append((cursor, _cell_text(tokens, cursor)))
                cursor += 1
            if in_head:
                header_rows.append(cells)
            else:
                body_rows.append(cells)
                body_positions.append(index)

    numeric_columns = _numeric_columns(body_rows)
    for cells in header_rows + body_rows:
        for column, (position, _) in enumerate(cells):
            if column in numeric_columns:
                tokens[position].attrJoin("class", "chat-num")

    for position, cells in zip(body_positions, body_rows, strict=True):
        if cells and cells[0][1].casefold().startswith(_TOTAL_ROW_PREFIX):
            tokens[position].attrJoin("class", "chat-total")


class _ChatMarkdown(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        """Разрешает веб-ссылки, почту и относительные адреса."""
        return (
            re.match(r"^(?!https?:|mailto:)[a-z][a-z0-9+.-]*:", url.strip(), re.IGNORECASE) is None
        )

    def render(self, src: str, env: MutableMapping[str, Any] | None = None) -> str:
        """Размечает таблицы КБЖУ для узких экранов и рендерит их."""
        tokens = self.parse(src, env)
        for index, token in enumerate(tokens):
            if token.type != "table_open":
                continue
            end = index + 1
            while end < len(tokens) and tokens[end].type != "table_close":
                end += 1
            _decorate_table(tokens, index, end)
        return self.renderer.render(tokens, self.options, env or {})


def _render_table_open(
    renderer: Any,
    tokens: Sequence[Token],
    index: int,
    options: OptionsDict,
    env: EnvType,
) -> str:
    return '<div class="chat-table-scroll"><table>\n'


def _render_table_close(
    renderer: Any,
    tokens: Sequence[Token],
    index: int,
    options: OptionsDict,
    env: EnvType,
) -> str:
    return "</table>\n</div>\n"


_parser = _ChatMarkdown("commonmark", {"html": False, "breaks": True}).enable("table")
# Таблица прокручивается внутри ответа, поэтому нужен контейнер вокруг `<table>`.
_parser.add_render_rule("table_open", _render_table_open)
_parser.add_render_rule("table_close", _render_table_close)


@register.filter
def markdown(value: str) -> SafeString:
    """Экранирует HTML, запрещает опасные ссылки и отображает разметку ответа."""
    return mark_safe(_parser.render(value))
