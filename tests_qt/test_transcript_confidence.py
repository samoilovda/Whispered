"""R6: low-confidence segments are underlined with dots, counted, and
stepped through; correcting one in edit mode clears its flag."""

from __future__ import annotations

from core.i18n import load_locale
from transcriber import Segment, TranscriptionResult
from ui.transcript_view import TranscriptView


def _view(process_events) -> TranscriptView:
    load_locale("en")
    view = TranscriptView()
    view.resize(900, 500)
    view.show()
    view._show_timestamps = False
    view.set_result(TranscriptionResult(segments=[
        Segment(0.0, 2.0, "Clear words.", confidence=0.95),
        Segment(2.0, 4.0, "mumbled bit", confidence=0.55),
        Segment(4.0, 6.0, "Old record text."),
        Segment(6.0, 8.0, "another blur", confidence=0.6),
    ], language="en", duration=8.0))
    process_events()
    return view


def test_uncertain_segments_are_marked_counted_and_stepped(process_events):
    view = _view(process_events)
    assert view.uncertain_btn.isVisible()
    assert view.uncertain_btn.text() == "◌ 2 uncertain passages"
    dotted = [
        sel.cursor.selectedText() for sel in view.text_edit.extraSelections()
        if sel.format.underlineStyle().name == "DotLine"
    ]
    assert dotted == ["mumbled bit", "another blur"]
    seeks = []
    view.seek_requested.connect(seeks.append)
    view.next_uncertain()
    assert view.text_edit.textCursor().selectedText() == "mumbled bit"
    view.next_uncertain()
    assert view.text_edit.textCursor().selectedText() == "another blur"
    assert seeks == [2.0, 6.0]
    view.close()


def test_correcting_a_segment_clears_its_flag(process_events):
    view = _view(process_events)
    view._enter_edit_mode()
    text = view.text_edit.toPlainText().replace("mumbled bit", "mumbled bits")
    view.text_edit.setPlainText(text)
    view._exit_edit_mode(save=True)
    confidences = [s.confidence for s in view.get_result().segments]
    assert confidences == [0.95, None, None, 0.6]
    view.close()
