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


def test_queue_chip_is_hidden_while_nothing_is_queued(process_events):
    """"Queue 0" used to be a permanent button to an empty panel."""
    from core.i18n import load_locale

    load_locale("en")
    bar = StatusBar()
    bar.show()
    process_events()
    assert not bar.queue_button.isVisibleTo(bar)

    bar.set_queue_count(2)
    bar.set_course_count(1)
    process_events()
    assert bar.queue_button.isVisibleTo(bar)
    assert bar.queue_button.text() == "3 tasks queued"

    bar.set_queue_count(0)
    bar.set_course_count(0)
    process_events()
    assert not bar.queue_button.isVisibleTo(bar)
    bar.close()


def test_queue_overlay_opens_with_the_chip_hidden(process_events):
    """The sidebar and View menu still open an empty queue: the card then
    anchors to the bar itself."""
    bar = StatusBar()
    bar.bind_queue(QWidget())
    bar.show()
    process_events()

    bar.show_queue(True)
    process_events()
    assert bar._overlay is not None and bar._overlay.isVisible()
    bar.show_queue(False)
    bar.close()


def test_llm_chip_follows_the_probe(process_events):
    from core.i18n import load_locale, tr

    load_locale("en")
    bar = StatusBar()
    assert bar.llm_chip.state() == "idle"
    assert bar.llm_chip.text() == "LM Studio"

    bar.set_llm_status(True, "google/gemma-4-12b")
    assert bar.llm_chip.state() == "ok"
    assert bar.llm_chip.text() == "gemma-4-12b"
    assert bar.llm_chip.toolTip() == "google/gemma-4-12b"

    bar.set_llm_status(True, "")
    assert bar.llm_chip.state() == "idle"

    bar.set_llm_status(False, "connection refused")
    assert bar.llm_chip.state() == "error"
    assert bar.llm_chip.text() == tr("status_llm_offline")
    bar.close()


def test_device_chip_names_the_model_and_backend_without_emoji(process_events):
    bar = StatusBar()
    bar.set_whisper_model("large-v3-turbo-q5_0")
    bar.set_device("metal", "Apple Metal (Apple Silicon)", True)
    assert bar.device_button.text() == "large-v3-turbo · Metal"

    bar.set_device("metal", "Apple Metal (Apple Silicon)", False)
    assert bar.device_button.text() == "large-v3-turbo · CPU"

    bar.set_device("cpu", "", True)
    assert bar.device_button.text() == "large-v3-turbo · CPU"
    bar.close()


def test_compute_popover_switches_the_device(process_events):
    from PyQt6.QtWidgets import QRadioButton

    bar = StatusBar()
    bar.set_whisper_model("large-v3-turbo-q5_0")
    bar.set_device("metal", "Apple Metal (Apple Silicon)", True)
    bar.show()
    process_events()
    picked: list[bool] = []
    bar.device_selected.connect(picked.append)

    bar.device_button.click()
    process_events()
    pop = bar._popover
    assert pop is not None and pop.isVisible()
    gpu, cpu = pop.findChildren(QRadioButton)
    assert gpu.isChecked() and gpu.text() == "Apple Metal (Apple Silicon)"

    cpu.setChecked(True)
    process_events()
    assert picked == [False]
    assert bar.device_button.text().endswith("· CPU")
    pop.close()
    bar.close()


def test_llm_popover_offers_recheck_and_settings(process_events):
    from PyQt6.QtWidgets import QPushButton

    from core.i18n import load_locale, tr

    load_locale("en")
    bar = StatusBar()
    bar.set_llm_status(False, "connection refused")
    bar.show()
    process_events()
    rechecks: list[bool] = []
    categories: list[str] = []
    bar.llm_refresh_requested.connect(lambda: rechecks.append(True))
    bar.settings_requested.connect(categories.append)

    bar.llm_chip.click()
    process_events()
    pop = bar._popover
    assert pop is not None and pop.isVisible()
    buttons = {b.text(): b for b in pop.findChildren(QPushButton)}
    buttons[tr("status_llm_recheck")].click()
    assert rechecks == [True]

    bar.llm_chip.click()
    process_events()
    buttons = {b.text(): b for b in bar._popover.findChildren(QPushButton)}
    buttons[tr("status_open_settings")].click()
    assert categories == ["settings_category_ai"]
    bar.close()
