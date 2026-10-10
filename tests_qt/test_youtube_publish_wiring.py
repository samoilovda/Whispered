"""MainWindow/RunView wiring for the YouTube publish dialog: it opens by
itself after a successful "youtube_package" step only when the user opted
in, and "Publish to YouTube" is always reachable from the finished run."""

from __future__ import annotations

from types import SimpleNamespace

from config import get_config
from domain.job import StepStatus


def _run(**statuses: StepStatus):
    return SimpleNamespace(outcomes={
        name: SimpleNamespace(status=status, error="", result=None)
        for name, status in statuses.items()
    })


def _finish(window, process_events, **statuses):
    window._on_recipe_job_finished(_run(**statuses))
    process_events()


def _window(monkeypatch, mode):
    from ui.main_window import MainWindow

    get_config().yt_publish_mode = mode
    window = MainWindow()
    opened = []
    monkeypatch.setattr(window.youtube_publish, "open_dialog", lambda: opened.append(True))
    return window, opened


def test_dialog_opens_after_a_successful_youtube_run_in_handoff_mode(monkeypatch, process_events):
    window, opened = _window(monkeypatch, "handoff")
    _finish(window, process_events, transcribe=StepStatus.SUCCEEDED,
            youtube_package=StepStatus.SUCCEEDED)
    assert opened == [True]
    assert window.run_view._publish_button.isVisibleTo(window.run_view)
    window.close()


def test_cached_youtube_step_also_counts(monkeypatch, process_events):
    window, opened = _window(monkeypatch, "handoff")
    _finish(window, process_events, youtube_package=StepStatus.SKIPPED)
    assert opened == [True]
    window.close()


def test_dialog_stays_closed_when_the_mode_is_off_but_the_button_is_offered(monkeypatch, process_events):
    window, opened = _window(monkeypatch, "off")
    _finish(window, process_events, youtube_package=StepStatus.SUCCEEDED)
    assert opened == []
    assert window.run_view._publish_button.isVisibleTo(window.run_view)
    window.close()


def test_failed_youtube_step_neither_opens_nor_offers_publishing(monkeypatch, process_events):
    window, opened = _window(monkeypatch, "handoff")
    _finish(window, process_events, transcribe=StepStatus.SUCCEEDED,
            youtube_package=StepStatus.FAILED)
    assert opened == []
    assert not window.run_view._publish_button.isVisibleTo(window.run_view)
    window.close()


def test_a_run_without_a_youtube_step_does_not_offer_publishing(monkeypatch, process_events):
    window, opened = _window(monkeypatch, "handoff")
    _finish(window, process_events, transcribe=StepStatus.SUCCEEDED)
    assert opened == []
    assert not window.run_view._publish_button.isVisibleTo(window.run_view)
    window.close()


def test_run_view_publish_button_emits_the_signal():
    from ui.run_view import RunView

    view = RunView(("transcribe",), {"transcribe": "Transcribe"})
    seen = []
    view.publish_requested.connect(lambda: seen.append(True))
    view.set_publish_available(True)
    assert not view._publish_button.isVisibleTo(view)   # not finished yet
    view.set_finished(True)
    assert view._publish_button.isVisibleTo(view)
    view._publish_button.click()
    assert seen == [True]
    view.set_finished(False)
    assert not view._publish_button.isVisibleTo(view)


def test_panel_button_follows_the_generated_state():
    from transcriber import Segment
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    panel.set_segments([Segment(0.0, 60.0, "hi")], transcript_language="en")
    assert not panel._publish_btn.isEnabled()
    panel.set_result({"yt_titles": ["T"], "yt_description": ["D"], "yt_tags": ["a"]})
    assert panel._publish_btn.isEnabled()
    seen = []
    panel.publish_requested.connect(lambda: seen.append(True))
    panel._publish_btn.click()
    assert seen == [True]
    panel.begin_generating()
    assert not panel._publish_btn.isEnabled()
    panel.close()


def test_publishing_with_nothing_generated_only_toasts(monkeypatch):
    from ui.main_window import MainWindow

    window = MainWindow()
    toasts = []
    monkeypatch.setattr(
        "ui.youtube_publish_controller.show_toast", lambda *a, **k: toasts.append(a[1]))
    window.youtube_publish.open_dialog()
    assert toasts and "YouTube" in toasts[0]
    window.close()
