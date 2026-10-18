"""Contrast of the theme's text roles (UI/UX audit 2026-10-18, UX-12 / T12a).

Measures the real token pairs the stylesheet renders — text color on the
backgrounds it sits on — with the WCAG 2.x relative-luminance formula.
4.5:1 for words that carry meaning, 3:1 for icons/borders/large text.
Disabled controls are deliberately exempt (they must look inert), but the
exemption never covers hints, warnings or statuses.
"""

from __future__ import annotations

import re

import pytest

from ui.theme import DARK, LIGHT, THEMES, Theme, build_stylesheet

_TEXT_MIN = 4.5
_NON_TEXT_MIN = 3.0


def _luminance(hex_color: str) -> float:
    channels = []
    for i in (1, 3, 5):
        c = int(hex_color[i:i + 2], 16) / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _blend(color: str, base: str, alpha: float) -> str:
    c = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(b[i] + (c[i] - b[i]) * alpha):02x}" for i in range(3))


def _page_backgrounds(t: Theme) -> list[str]:
    return [t.bg_base, t.bg_deep, t.bg_surface, t.bg_elevated]


_TEXT_ROLES = ("text_primary", "text_secondary", "text_muted",
               "accent_text", "success_text", "warning_text", "error_text", "info_text")


@pytest.mark.parametrize("theme", THEMES.values(), ids=list(THEMES))
@pytest.mark.parametrize("role", _TEXT_ROLES)
def test_text_roles_readable_on_every_page_background(theme: Theme, role: str):
    fg = getattr(theme, role)
    for bg in _page_backgrounds(theme):
        assert contrast(fg, bg) >= _TEXT_MIN, (
            f"{theme.name}.{role} {fg} on {bg}: {contrast(fg, bg):.2f}:1")


@pytest.mark.parametrize("theme", THEMES.values(), ids=list(THEMES))
@pytest.mark.parametrize("kind", ("info", "success", "warning", "error"))
def test_status_badge_text_readable_on_its_tint(theme: Theme, kind: str):
    # QLabel[role="status-*"]: rgba(fill, 0.14) behind *_text.
    fg = getattr(theme, f"{kind}_text")
    for page in _page_backgrounds(theme):
        bg = _blend(getattr(theme, kind), page, 0.14)
        assert contrast(fg, bg) >= _TEXT_MIN, f"{theme.name} status-{kind} on {bg}"


@pytest.mark.parametrize("theme", THEMES.values(), ids=list(THEMES))
def test_primary_button_text_readable(theme: Theme):
    # White label on the accent fill (primary buttons) and its pressed state.
    assert contrast("#ffffff", theme.accent) >= _TEXT_MIN
    assert contrast("#ffffff", theme.accent_pressed) >= _TEXT_MIN


@pytest.mark.parametrize("theme", THEMES.values(), ids=list(THEMES))
def test_disabled_text_still_distinguishable(theme: Theme):
    assert contrast(theme.text_disabled, theme.bg_disabled) >= _NON_TEXT_MIN


@pytest.mark.parametrize("theme", THEMES.values(), ids=list(THEMES))
def test_stylesheet_never_uses_a_fill_token_as_text_color(theme: Theme):
    # The fill tokens are tuned for backgrounds/icons; `color:` must go
    # through the *_text roles (light warning #f9ab00 on white is 1.9:1).
    qss = build_stylesheet(theme)
    for name in ("accent", "success", "warning", "error", "info"):
        fill = getattr(theme, name)
        text = getattr(theme, f"{name}_text")
        if fill.lower() == text.lower():
            continue
        offenders = re.findall(rf"(?<![-\w])color:\s*{re.escape(fill)}\b", qss, re.I)
        assert not offenders, f"{theme.name}: color: {fill} ({name}) used for text"


def test_audit_pairs_fixed():
    # The concrete failures measured in docs/research/uiux-2026-10-18.
    assert contrast(DARK.text_muted, DARK.bg_base) >= _TEXT_MIN       # was 3.03
    assert contrast(DARK.text_secondary, DARK.bg_surface) >= _TEXT_MIN  # was 4.05
    assert contrast(LIGHT.warning_text, "#ffffff") >= _TEXT_MIN      # was 1.93
    assert contrast(DARK.accent_text, DARK.bg_surface) >= _TEXT_MIN  # was 3.21
