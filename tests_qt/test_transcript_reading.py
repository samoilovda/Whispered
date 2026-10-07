"""Reading mode of ui/transcript_view.py: paragraphs, click-to-seek,
playback highlight that leaves the cursor alone, find with a counter."""

from __future__ import annotations

from transcriber import Segment, TranscriptionResult
from ui.transcript_view import TranscriptView


def _result() -> TranscriptionResult:
    segments = [
        Segment(0.0, 2.0, "Hello there."),
        Segment(2.0, 4.0, "General greeting."),
        Segment(10.0, 12.0, "After a pause the topic changes completely and keeps going for a while."),
        Segment(12.0, 14.0, "Another hello."),
    ]
    return TranscriptionResult(segments=segments, language="en", duration=14.0)


def _view(process_events) -> TranscriptView:
    view = TranscriptView()
    view.resize(900, 600)
    view.show()
    view._show_timestamps = False
    view.set_result(_result())
    process_events()
    return view


def test_segments_flow_into_paragraphs(process_events):
    view = _view(process_events)
    text = view.get_text()
    assert "Hello there. General greeting." in text
    assert len(view._segment_spans) == 4
    view.close()


def test_highlight_follows_time_without_moving_the_cursor(process_events):
    view = _view(process_events)
    cursor_before = view.text_edit.textCursor().position()
    view.highlight_at(0.5)   # first tick: nothing moved yet
    view.highlight_at(11.0)  # moved → playing
    assert view._highlighted_index == 2
    assert view.text_edit.textCursor().position() == cursor_before
    selections = view.text_edit.extraSelections()
    assert selections and selections[0].cursor.selectedText().startswith("After a pause")
    view.close()


def test_a_click_on_a_segment_seeks_to_it(process_events):
    view = _view(process_events)
    seeks = []
    view.seek_requested.connect(seeks.append)
    span = view._segment_spans[3]
    cursor = view.text_edit.textCursor()
    cursor.setPosition(span.pos_from + 2)
    view._seek_at_point(view.text_edit.cursorRect(cursor).center())
    assert seeks == [12.0]
    view.close()


def test_find_counts_and_steps_through_matches(process_events):
    view = _view(process_events)
    view._open_find()
    view._find_edit.setText("hello")
    process_events()
    assert len(view._find_matches) == 2
    assert view._find_count.text() == "1 of 2"
    view._find_next()
    assert view._find_count.text() == "2 of 2"
    view._find_next()
    assert view._find_count.text() == "1 of 2"
    view._find_previous()
    assert view._find_count.text() == "2 of 2"
    view._close_find()
    assert view._find_matches == []
    view.close()


def test_replace_one_changes_the_segment_with_the_current_match(process_events):
    view = _view(process_events)
    view._open_find()
    view._find_edit.setText("hello")
    view._find_next()  # second match: "Another hello."
    view._replace_edit.setText("hi")
    view._replace_one()
    assert [s.text for s in view.get_result().segments][3] == "Another hi."
    assert view.get_result().segments[0].text == "Hello there."
    view.close()
