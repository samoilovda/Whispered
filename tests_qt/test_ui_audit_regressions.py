"""Regression coverage for docs/UI_UX_AUDIT_2026-08.md P0 items 1, 2, 4.

These are geometry/lifecycle bugs that are easy to reintroduce silently and
cheap to pin down with a real (offscreen) QApplication.
"""

from __future__ import annotations


def test_workspace_columns_expand_above_force_threshold(process_events):
    """A roomy workspace must honour expanded Library and Inspector choices."""
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    cfg = get_config()
    cfg.library_collapsed = False
    cfg.inspector_collapsed = False

    window.resize(1100, 700)
    process_events()

    assert window.library_view.isVisible() is True
    assert window.inspector.is_collapsed() is False
    assert cfg.library_collapsed is False
    assert cfg.inspector_collapsed is False

    window.close()
    process_events()


def test_workspace_force_compact_does_not_overwrite_saved_choice(process_events):
    """Narrow widths compact both edge columns without persisting the force."""
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    cfg = get_config()
    cfg.library_collapsed = False
    cfg.inspector_collapsed = False

    window.resize(900, 550)
    process_events()
    assert window.library_view.isVisible() is False
    assert window.inspector.is_collapsed() is True
    assert cfg.library_collapsed is False
    assert cfg.inspector_collapsed is False

    window.resize(1100, 700)
    process_events()
    assert window.library_view.isVisible() is True
    assert window.inspector.is_collapsed() is False

    window.close()
    process_events()


def test_narrow_workspace_edge_rails_open_without_persisting_force(process_events):
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.resize(900, 550)
    process_events()
    cfg = get_config()
    cfg.library_collapsed = False
    cfg.inspector_collapsed = False

    window.workspace_shell.new_button.click()
    process_events()
    assert window.library_view.isVisible() is True
    assert cfg.library_collapsed is False

    window.inspector.set_collapsed(True, emit=False)
    window.inspector._buttons["settings"].click()
    process_events()
    assert window.inspector.is_collapsed() is False
    assert cfg.inspector_collapsed is False

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


def test_step_checklist_keeps_transcript_mandatory(process_events):
    from ui.step_checklist import StepChecklist

    checklist = StepChecklist()
    checklist.show()
    process_events()

    assert checklist._checks["transcript"].isChecked()
    assert checklist._checks["transcript"].isEnabled() is False
    assert "transcript" in checklist.selected_steps()

    checklist.close()
