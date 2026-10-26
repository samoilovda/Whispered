"""Long operations keep a moving clock on screen, so an LLM call that sits
on one percentage for minutes does not look hung: the status bar's
elapsed time, a running recipe step's own time, and the chat's
"thinking" bubble before the first word."""

from __future__ import annotations

import time

from core.i18n import set_locale
from ui.status_bar import StatusBar


def test_status_bar_shows_elapsed_time_while_busy(monkeypatch):
    set_locale("en")
    bar = StatusBar()
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    bar._tick()
    assert bar.elapsed_label.isHidden()            # idle: no clock
    bar.set_operation("Generating")
    bar._tick()
    now[0] += 2
    bar._tick()
    assert bar.elapsed_label.isHidden()            # a quick operation: none yet
    now[0] += 63
    bar._tick()
    assert not bar.elapsed_label.isHidden()
    assert bar.elapsed_label.text() == "1:05 elapsed"
    bar.clear()
    bar._tick()
    assert bar.elapsed_label.isHidden()


def test_a_running_step_shows_its_own_time(monkeypatch):
    from ui.run_view import RunStepRow

    set_locale("en")
    now = [500.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    row = RunStepRow("insights", "Insights")
    row.set_running()
    row.set_progress(16, "Titles (2 of 6)…")
    now[0] += 75
    row.tick()
    assert row._status_badge.text().endswith("Titles (2 of 6)… · 1:15")


def test_chat_says_it_is_thinking_until_the_first_word(monkeypatch):
    from ui.chat_panel import ChatPanel

    set_locale("en")
    panel = ChatPanel()
    now = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])

    class _Running:
        def isRunning(self):
            return True

    panel._worker = _Running()
    panel._current_bubble = panel._add_bubble("…", "assistant")
    panel._token_buf = []
    panel._wait_started = now[0]
    now[0] += 12
    panel._tick_thinking()
    assert panel._current_bubble.text() == "Thinking… 0:12"
    panel._on_token("Hello")
    panel._tick_thinking()
    assert panel._current_bubble.text() == "Hello"
