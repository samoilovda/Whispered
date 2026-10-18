"""The chat says what of the record the model sees and links only the
citations it can back up (UI/UX audit 2026-10-18, UX-13 / T13)."""

from __future__ import annotations

import config
from core.i18n import load_locale, tr
from transcriber import Segment, TranscriptionResult


def _segments(count, seconds_each=30.0, words=40):
    return [Segment(i * seconds_each, (i + 1) * seconds_each, f"part {i} " + "word " * words)
            for i in range(count)]


def _panel(monkeypatch, segments, context_chars=48_000):
    from ui.chat_panel import ChatPanel

    load_locale("en")
    monkeypatch.setattr(config, "_config", config.Config(chat_context_chars=context_chars))
    panel = ChatPanel()
    result = TranscriptionResult(segments=segments, language="en", duration=segments[-1].end)
    panel.set_record_context(None, "r1", result.full_text, segments, persist=False)
    return panel


def test_short_transcript_is_seen_whole_and_its_times_link(monkeypatch, process_events):
    panel = _panel(monkeypatch, _segments(4))
    assert panel._scope_label.text() == tr("chat_scope_full", total=4)
    bubble = panel._add_bubble("See [01:05].", "assistant")
    assert "(seek:65)" in super(type(bubble), bubble).text()


def test_long_transcript_says_it_is_sampled_and_flags_unseen_times(monkeypatch, process_events):
    segs = _segments(400)
    panel = _panel(monkeypatch, segs, context_chars=20_000)
    context = panel._context
    assert context.sampled
    assert panel._scope_label.text() == tr(
        "chat_scope_sampled", used=len(context.kept_blocks), total=context.total_blocks)
    # The end of the recording is always in the sample.
    last_start = int(segs[-1].start)
    assert panel.verify_citation(last_start + 5)
    # A middle block that was not sampled, and a time past the end.
    unseen = next(i for i in range(context.total_blocks) if i not in context.kept_blocks)
    from core.llm_text import block_start_seconds

    start = block_start_seconds(panel._context_lines[unseen])
    assert not panel.verify_citation(start + 1)
    assert not panel.verify_citation(segs[-1].end + 3600)
    bubble = panel._add_bubble("At [9:59:00] and nowhere else.", "assistant")
    shown = super(type(bubble), bubble).text()
    assert "(?)" in shown and "seek:" not in shown


def test_question_carries_the_context_the_label_describes(monkeypatch, process_events):
    from core.chat_worker import chat_context

    segs = _segments(400)
    panel = _panel(monkeypatch, segs, context_chars=20_000)
    expected = chat_context(panel._transcript, 20_000, panel._context_lines)
    assert panel._context.prompt == expected.prompt
