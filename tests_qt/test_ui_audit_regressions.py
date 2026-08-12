"""Regression coverage for docs/UI_UX_AUDIT_2026-08.md P0 items 1, 2, 4.

These are geometry/lifecycle bugs that are easy to reintroduce silently and
cheap to pin down with a real (offscreen) QApplication.
"""

from __future__ import annotations


def test_sidebar_expand_survives_resize_above_force_threshold(process_events):
    """A resize above the force-collapse floor must defer to the user's
    saved choice, not silently re-collapse an explicit expand click."""
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    cfg = get_config()
    cfg.sidebar_collapsed = False
    window.sidebar.set_collapsed(False, emit=False)

    window.resize(1100, 700)
    process_events()

    assert window.sidebar._collapsed is False
    assert cfg.sidebar_collapsed is False

    window.close()
    process_events()


def test_sidebar_force_collapse_does_not_overwrite_saved_choice(process_events):
    """Narrow widths force a visual collapse but must not persist that as
    the user's preference — widening back out should restore expanded."""
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    cfg = get_config()
    cfg.sidebar_collapsed = False
    window.sidebar.set_collapsed(False, emit=False)

    window.resize(900, 550)
    process_events()
    assert window.sidebar._collapsed is True
    assert cfg.sidebar_collapsed is False

    window.resize(1100, 700)
    process_events()
    assert window.sidebar._collapsed is False

    window.close()
    process_events()


def test_batch_panel_shows_only_empty_state_on_cold_start(process_events):
    """Cold start must not show both the empty file_list frame and the
    empty-state placeholder at once."""
    from ui.batch_panel import BatchPanel

    panel = BatchPanel()
    panel.show()
    process_events()

    assert panel.file_list.isVisible() is False
    assert panel.empty_state.isVisible() is True

    panel.close()
    process_events()


def test_launch_bar_summary_label_never_exceeds_its_own_width(process_events):
    """The summary label must elide instead of overflowing its container,
    for both a roomy and a cramped bar width."""
    from ui.launch_bar import LaunchBar

    bar = LaunchBar()
    bar._summary_full_text = (
        "самая компактная и быстрая конфигурация · ⚡ Быстрый прогон"
    )
    bar.show()
    process_events()

    for width in (900, 700, 500):
        bar.resize(width, bar.height())
        process_events()
        assert bar.summary_label.width() <= bar.width()
        text = bar.summary_label.text()
        assert text != ""
        # Elided text must be a clean prefix of the full string (plus an
        # ellipsis), never a hard cut mid-word/mid-glyph.
        prefix = text.rstrip("…")
        assert bar._summary_full_text.startswith(prefix)

    bar.close()
    process_events()
