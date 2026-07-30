"""Real-Qt smoke coverage for MainWindow's top-level sections.

test_main_window_smoke.py only ever constructs/closes the window on its
default page (Library). These exercise the other sections the sidebar
switches to — Queue, Recorder, Live — plus the Record page reached after
finishing a transcription, checking each is safe to navigate to, show,
and close from without leaving a QThread running.
"""

from PyQt6.QtCore import QThread


def _running_threads(window):
    return [t for t in window.findChildren(QThread) if t.isRunning()]


def test_library_is_the_default_section(process_events):
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    assert window._stack.currentIndex() == window._section_index["library"]

    window.close()
    process_events()


def test_navigating_to_queue_shows_the_batch_panel_page(process_events):
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    window._on_section_changed("queue")
    process_events()

    assert window._stack.currentIndex() == window._section_index["queue"]
    assert window.batch_panel.isVisibleTo(window._stack.currentWidget())

    window.close()
    process_events()


def test_navigating_to_recorder_shows_the_recorder_widget_page(process_events):
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    window._on_section_changed("recorder")
    process_events()

    assert window._stack.currentIndex() == window._section_index["recorder"]
    assert window.recorder_widget.isVisibleTo(window._stack.currentWidget())

    window.close()
    process_events()


def test_navigating_to_live_section_does_not_touch_target_discovery(process_events):
    """WHISPERED_UI_GALLERY=1 (set by qt_application) must suppress the
    real refresh_targets() call _on_section_changed makes otherwise —
    that would try to launch the ScreenCaptureKit helper process."""
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    window._on_section_changed("live")
    process_events()

    assert window._stack.currentIndex() == window._section_index["live"]
    assert window._stack.currentWidget() is window.live_view

    window.close()
    process_events()


def test_record_section_reachable_after_finishing_a_transcription(process_events):
    from transcriber import Segment, TranscriptionResult
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    result = TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")],
        language="en",
        duration=1.0,
    )
    window._on_finished(result, open_record=True, save_history=False)
    process_events()

    assert window._stack.currentIndex() == window._record_index
    assert window.transcript_view.get_result() is result

    window.close()
    process_events()


def test_returning_to_library_from_the_record_page(process_events):
    from transcriber import Segment, TranscriptionResult
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    result = TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hello world")],
        language="en",
        duration=1.0,
    )
    window._on_finished(result, open_record=True, save_history=False)
    process_events()
    assert window._stack.currentIndex() == window._record_index

    window._show_library()
    process_events()

    assert window._stack.currentIndex() == window._section_index["library"]

    window.close()
    process_events()


def test_no_threads_left_running_after_closing_from_any_section(process_events):
    from ui.main_window import MainWindow

    for key in ("library", "queue", "recorder", "live"):
        window = MainWindow()
        window.show()
        process_events()

        window._on_section_changed(key)
        process_events()

        window.close()
        process_events()

        assert _running_threads(window) == [], f"threads leaked closing from {key!r}"
