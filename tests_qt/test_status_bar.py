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

from ui.status_bar import StatusBar


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
