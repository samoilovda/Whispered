"""Minimal real-Qt lifecycle regression tests."""

from PyQt6.QtCore import QThread


def test_main_window_constructs_and_closes(process_events):
    """The primary window must be safe to construct in a headless Qt app."""
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()
    window.close()
    process_events()

    assert not [
        thread for thread in window.findChildren(QThread) if thread.isRunning()
    ]


def test_replace_all_updates_the_result_not_only_the_document(process_events):
    """Copy/export consumers must see the same text as the visible editor."""
    from transcriber import Segment, TranscriptionResult
    from ui.transcript_view import TranscriptView

    result = TranscriptionResult(
        segments=[Segment(0.0, 1.0, "alpha beta")],
        language="en",
        duration=1.0,
    )
    view = TranscriptView()
    view.set_result(result)
    view._find_edit.setText("beta")
    view._replace_edit.setText("GAMMA")
    view._replace_all()
    process_events()

    assert result.full_text == "alpha GAMMA"
    assert view.get_text().endswith("alpha GAMMA")
