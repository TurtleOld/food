"""Контраст и фокус страницы дня: критерии WCAG AA проверяются по таблице стилей.

Соотношение контраста живёт в CSS, а не в ответе HTTP, поэтому здесь читается
тот же файл, что отдаёт приложение, — как в `test_shell` для тёмной темы.
"""

import re
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

AA_TEXT = 4.5
AA_NON_TEXT = 3.0

DECLARATION = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
HSL = re.compile(r"hsl\(\s*(\d+)\s+(\d+)%\s+([\d.]+)%\s*\)")
HEX = re.compile(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


def _css() -> str:
    path = finders.find("css/app.css")
    assert isinstance(path, str)
    return Path(path).read_text()


def _rule(css: str, selector: str) -> str:
    opening = re.search(rf"(?m)^{re.escape(selector)}\s*\{{", css)
    assert opening is not None, selector
    start = opening.end() - 1
    return css[start : css.index("}", start)]


def _tokens(css: str, scheme: str = "light") -> dict[str, str]:
    """Объявления `--token` из `:root`; для тёмной схемы их дополняет dark-блок."""
    media_at = css.index("@media (prefers-color-scheme: dark)")
    tokens = dict(DECLARATION.findall(css[:media_at]))
    if scheme == "dark":
        root_at = css.index(":root {", media_at)
        tokens.update(DECLARATION.findall(css[root_at : css.index("}", root_at)]))
    return tokens


def _rgb(value: str) -> tuple[int, int, int]:
    hsl = HSL.fullmatch(value.strip())
    if hsl is not None:
        hue, saturation, lightness = int(hsl[1]), int(hsl[2]) / 100, float(hsl[3]) / 100
        chroma = (1 - abs(2 * lightness - 1)) * saturation
        second = chroma * (1 - abs((hue / 60) % 2 - 1))
        offset = lightness - chroma / 2
        red, green, blue = [
            (chroma, second, 0),
            (second, chroma, 0),
            (0, chroma, second),
            (0, second, chroma),
            (second, 0, chroma),
            (chroma, 0, second),
        ][hue // 60]
        return (
            round((red + offset) * 255),
            round((green + offset) * 255),
            round((blue + offset) * 255),
        )
    hex_color = HEX.fullmatch(value.strip())
    assert hex_color is not None, value
    digits = hex_color[1]
    if len(digits) == 3:
        digits = "".join(digit * 2 for digit in digits)
    return (
        int(digits[0:2], 16),
        int(digits[2:4], 16),
        int(digits[4:6], 16),
    )


def _luminance(color: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        ratio = value / 255
        return ratio / 12.92 if ratio <= 0.04045 else ((ratio + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in color)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(foreground: str, background: str) -> float:
    first, second = _luminance(_rgb(foreground)), _luminance(_rgb(background))
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _declaration(rule: str, name: str) -> str:
    match = re.search(rf"(?:^|[\s;]){name}:\s*([^;]+);", rule)
    assert match is not None, name
    return match.group(1).strip()


def _resolve(tokens: dict[str, str], value: str) -> str:
    reference = re.fullmatch(r"var\((--[\w-]+)\)", value)
    return tokens[reference[1]] if reference is not None else value


class DayPageContrastTest(SimpleTestCase):
    """Текст страницы дня проходит WCAG AA в обеих схемах."""

    def test_primary_add_button_text_passes_aa(self):
        css = _css()
        tokens = _tokens(css)
        rule = _rule(css, ".btn-main")

        background = _resolve(tokens, _declaration(rule, "background"))
        self.assertGreaterEqual(_contrast(_declaration(rule, "color"), background), AA_TEXT)

    def test_muted_captions_pass_aa_in_light_and_dark_schemes(self):
        css = _css()

        for scheme in ("light", "dark"):
            with self.subTest(scheme=scheme):
                tokens = _tokens(css, scheme)
                self.assertGreaterEqual(
                    _contrast(tokens["--bulma-text-weak"], tokens["--page"]), AA_TEXT
                )

    def test_over_goal_text_passes_aa_in_light_and_dark_schemes(self):
        css = _css()

        for scheme in ("light", "dark"):
            with self.subTest(scheme=scheme):
                tokens = _tokens(css, scheme)
                self.assertGreaterEqual(_contrast(tokens["--over-goal"], tokens["--page"]), AA_TEXT)

    def test_focus_ring_stands_out_on_the_page_in_both_schemes(self):
        css = _css()

        for scheme in ("light", "dark"):
            with self.subTest(scheme=scheme):
                tokens = _tokens(css, scheme)
                self.assertGreaterEqual(
                    _contrast(tokens["--focus-ring"], tokens["--page"]), AA_NON_TEXT
                )

    def test_danger_text_passes_aa_on_cards_and_page(self):
        css = _css()

        for scheme, surface in (("light", "#fff"), ("dark", "var(--page)")):
            with self.subTest(scheme=scheme):
                tokens = _tokens(css, scheme)
                background = tokens["--page"] if surface == "var(--page)" else surface
                self.assertGreaterEqual(_contrast(tokens["--danger-text"], background), AA_TEXT)

    def test_error_toast_uses_the_danger_token(self):
        css = _css()
        rule = _rule(css, ".toast.is-error")

        self.assertEqual(_declaration(rule, "color"), "var(--danger-text)")


class FocusStyleTest(SimpleTestCase):
    """Одна и та же обводка фокуса на всех интерактивных элементах."""

    def test_every_focus_outline_uses_the_shared_token(self):
        outlines = {value.strip() for value in re.findall(r"outline:\s*([^;]+);", _css())}

        self.assertEqual(outlines, {"none", "2px solid var(--focus-ring)"})
