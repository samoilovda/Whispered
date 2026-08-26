"""Real-Qt tests for ui/status_bar.py's set_busy() (see
docs/IMPROVEMENT_PLAN_2026-08.ru.md, A7).

Regression basis: StatusBar.setVisible() used to be overridden to always
force True regardless of the argument, so a caller writing
``self.operation_bar.setVisible(False)`` had no effect — nothing actually
depended on hiding the whole bar (see the module docstring's "Infrastructure
status is intentionally always present"), only on the progress bar and
Cancel button it hosts. That override is gone; setVisible() now behaves
like any QWidget's, and set_busy() names the real intent.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from ui.status_bar import StatusBar, _QueueOverlay


def test_set_busy_shows_progress_and_cancel_together(process_events):
    bar = StatusBar()
    bar.show()
    process_events()

    assert not bar.progress.isVisibleTo(bar)
    assert not bar.cancel_button.isVisibleTo(bar)

    bar.set_busy(True)
    process_events()
    assert bar.progress.isVisibleTo(bar)
    assert bar.cancel_button.isVisibleTo(bar)

    bar.set_busy(False)
    process_events()
    assert not bar.progress.isVisibleTo(bar)
    assert not bar.cancel_button.isVisibleTo(bar)

    bar.close()


def test_set_busy_does_not_touch_the_bars_own_visibility(process_events):
    """The frame itself is infrastructure status and stays wherever its
    caller put it — set_busy() only toggles the indicators it hosts, and
    must not fight a caller that explicitly hid the whole bar."""
    bar = StatusBar()
    bar.show()
    bar.setVisible(False)
    process_events()
    assert not bar.isVisible()

    bar.set_busy(True)
    process_events()
    assert not bar.isVisible()

    bar.close()


def test_setvisible_is_no_longer_overridden_to_lie(process_events):
    """Regression: setVisible(False) used to be silently ignored."""
    bar = StatusBar()
    bar.show()
    process_events()
    assert bar.isVisible()

    bar.setVisible(False)
    process_events()
    assert not bar.isVisible()

    bar.close()


def test_set_operation_resets_a_custom_cancel_label_when_none_is_given(
    process_events,
):
    """Regression (A9(d)): a caller that set a custom cancel_text on one
    operation used to leave it on the button for the next operation that
    didn't pass one — the old ``if cancel_text:`` guard only ever set the
    label, never restored it."""
    from core.i18n import load_locale, tr

    load_locale("en")
    bar = StatusBar()
    bar.show()
    process_events()

    bar.set_operation("Recording", cancel_text="Stop recording")
    process_events()
    assert bar.cancel_button.text() == "Stop recording"

    bar.set_operation("Transcribing")
    process_events()
    assert bar.cancel_button.text() == tr("btn_cancel")

    bar.close()


def test_queue_overlay_stays_on_screen_for_an_anchor_near_the_top(process_events):
    """Regression (A9(e)): show_above() positioned the 420x460 card purely
    relative to its anchor, with no floor on the resulting y — an anchor
    close to the top of the screen pushed it partly (or, on a short
    screen, entirely) above available.top()."""
    from PyQt6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    assert screen is not None, "no screen available under this QPA platform"
    available = screen.availableGeometry()

    anchor = QWidget()
    anchor.setGeometry(available.left() + 10, available.top() + 2, 80, 24)
    anchor.show()
    process_events()

    batch_widget = QWidget()
    overlay = _QueueOverlay(batch_widget)
    overlay.show_above(anchor)
    process_events()

    geometry = overlay.frameGeometry()
    assert geometry.top() >= available.top()
    assert geometry.left() >= available.left()
    assert geometry.right() <= available.right()

    overlay.close()
    anchor.close()
