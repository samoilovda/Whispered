"""Chat answers cite [MM:SS]; a citation is a link that seeks the player."""

from __future__ import annotations

from transcriber import Segment, TranscriptionResult


def test_citation_link_seeks_and_history_keeps_the_raw_text(process_events):
    from ui.chat_panel import ChatPanel

    panel = ChatPanel()
    result = TranscriptionResult(segments=[Segment(0.0, 5.0, "hello")], language="en", duration=5.0)
    panel.set_transcript(result.full_text, result.segments)
    assert panel._context_lines == ["[00:00] hello"]

    bubble = panel._add_bubble("It is said at [01:05].", "assistant")
    assert bubble.text() == "It is said at [01:05]."
    seeks = []
    panel.seek_requested.connect(seeks.append)
    bubble.linkActivated.emit("seek:65")
    assert seeks == [65.0]


def test_window_wires_citations_to_the_player(monkeypatch, process_events):
    seeks = []
    monkeypatch.setattr(
        "ui.player_widget.PlayerWidget.seek_to", lambda self, seconds: seeks.append(seconds),
    )
    from ui.main_window import MainWindow

    window = MainWindow()
    window.chat_panel.seek_requested.emit(12.0)
    assert seeks == [12.0]
    window.close()
    process_events()
