"""The stylesheet must not change text metrics in a pseudo-state.

Qt sizes a widget from the font it resolves *outside* pseudo-states, so a
rule like ``QPushButton:checked { font-weight: bold; }`` paints text wider
than the width the layout reserved and the label is clipped — while every
size-based check still passes, because the widget really is as wide as its
own sizeHint() says. That is how the start screen's selected recipe chip
came to render "Только расшифровка" as "олько расшифровк".
"""

from __future__ import annotations

import re

import pytest

from ui.theme import THEMES, build_stylesheet

# A pseudo-state (":checked", ":hover", …) restyles an existing widget.
# A sub-control ("::tab", "::item", …) styles a part Qt measures itself,
# and a plain state-free selector feeds the widget font as usual — only
# the first kind can desynchronise painting from sizing.
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
_FONT_METRIC = re.compile(r"\bfont(-size|-weight|-family|-style)?\s*:")
_PSEUDO_STATE = re.compile(r"(?<!:):(?!:)[a-z-]+")


def _selectors_changing_font_in_a_pseudo_state(sheet: str) -> list[str]:
    offenders = []
    for selector, body in _RULE.findall(sheet):
        selector = selector.strip()
        if not selector or not _FONT_METRIC.search(body):
            continue
        without_sub_controls = re.sub(r"::[a-z-]+", "", selector)
        # Attribute selectors carry colons of their own (role="page-title"
        # never does, but keep the check honest about it).
        without_attributes = re.sub(r"\[[^\]]*\]", "", without_sub_controls)
        if _PSEUDO_STATE.search(without_attributes):
            offenders.append(selector)
    return offenders


@pytest.mark.parametrize("theme_name", sorted(THEMES))
def test_no_pseudo_state_rule_changes_font_metrics(theme_name):
    sheet = build_stylesheet(THEMES[theme_name])
    offenders = _selectors_changing_font_in_a_pseudo_state(sheet)
    assert not offenders, (
        "these rules change text metrics in a pseudo-state, which Qt "
        f"cannot feed back into sizeHint(): {offenders}"
    )


def test_the_check_would_catch_the_rule_it_was_written_for():
    sheet = 'QPushButton[role="quick-chip"]:checked { font-weight: bold; }'
    assert _selectors_changing_font_in_a_pseudo_state(sheet)


def test_the_check_leaves_state_free_and_sub_control_rules_alone():
    assert not _selectors_changing_font_in_a_pseudo_state(
        'QTabBar::tab { font-size: 13px; }'
    )
    assert not _selectors_changing_font_in_a_pseudo_state(
        'QLabel[role="page-title"] { font-weight: bold; }'
    )
    assert not _selectors_changing_font_in_a_pseudo_state(
        'QPushButton:checked { background-color: #eee; }'
    )
