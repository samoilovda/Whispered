"""Regression coverage for docs/UI_UX_AUDIT_2026-08.md P0 items 1, 2, 4.

These are geometry/lifecycle bugs that are easy to reintroduce silently and
cheap to pin down with a real (offscreen) QApplication.
"""

from __future__ import annotations


def test_workspace_library_expands_above_force_threshold(process_events):
    """A roomy workspace must honour an expanded Library choice."""
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    cfg = get_config()
    cfg.library_collapsed = False

    window.resize(1100, 700)
    process_events()

    assert window.library_view.isVisible() is True
    assert cfg.library_collapsed is False

    window.close()
    process_events()


def test_workspace_force_compact_does_not_overwrite_saved_choice(process_events):
    """A narrow width compacts the Library column without persisting the
    force — see ui/workspace_shell.py's FORCE_COMPACT_WIDTH, kept above
    MainWindow's own 900px minimum so this is reachable at all."""
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    cfg = get_config()
    cfg.library_collapsed = False

    window.resize(900, 550)
    process_events()
    assert window.library_view.isVisible() is False
    assert cfg.library_collapsed is False

    window.resize(1100, 700)
    process_events()
    assert window.library_view.isVisible() is True

    window.close()
    process_events()


def test_narrow_workspace_library_opens_without_persisting_force(process_events):
    from config import get_config
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.resize(900, 550)
    process_events()
    cfg = get_config()
    cfg.library_collapsed = False

    window.workspace_shell.new_button.click()
    process_events()
    assert window.library_view.isVisible() is True
    assert cfg.library_collapsed is False

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


def test_transcribe_options_persist_the_users_choice(process_events, tmp_path, monkeypatch):
    """Regression: since the redesign the recipe editor is the only place
    outside Settings to pick a Whisper model/language/mode, and nothing
    wrote the choice back to Config — every selection was lost on the
    next launch, silently reverting to Settings' defaults."""
    import config
    from ui.transcribe_options import TranscribeOptionsPopover

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())

    options = TranscribeOptionsPopover(embedded=True)
    process_events()
    # Seeding from Config must not itself write anything back out.
    assert not (tmp_path / "config.json").exists()

    target = next(
        i for i in range(options.model_combo.count())
        if options.model_combo.itemData(i) != config.get_config().default_model
    )
    options.model_combo.setCurrentIndex(target)
    chosen = options.model_combo.itemData(target)
    options.diarization_checkbox.setChecked(True)
    process_events()

    assert config.get_config().default_model == chosen
    assert config.get_config().diarization_enabled is True
    assert config.Config.load().default_model == chosen

    options.close()


def test_recipe_editor_keeps_transcribe_mandatory(process_events):
    from domain.recipe import TRANSCRIPT_ONLY
    from ui.recipe_editor import RecipeEditorDialog
    from ui.transcribe_options import TranscribeOptionsPopover

    options = TranscribeOptionsPopover(embedded=True)
    dialog = RecipeEditorDialog(options, TRANSCRIPT_ONLY)
    process_events()

    assert dialog._checks["transcribe"].isChecked()
    assert dialog._checks["transcribe"].isEnabled() is False
    assert "transcribe" in dialog.selected_steps()

    options.setParent(None)
    dialog.close()


def test_library_filter_chips_not_narrower_than_their_text(process_events):
    """Regression for docs/UI_UX_AUDIT_2026-08.md P1 item 8 / the clipped
    filter chips found in the 2026-09 gallery review ("Диктофон" -> "iктоф"
    at 1100x700): a chip must never be narrower than what its own label
    needs, in either locale. Library's filter row now wraps to a second
    row (ui.components.FlowLayout) instead of squeezing chips below their
    sizeHint(), same as a QHBoxLayout would.
    """
    from core.i18n import load_locale
    from PyQt6.QtWidgets import QPushButton
    from ui.main_window import MainWindow

    for language in ("ru", "en"):
        load_locale(language)
        window = MainWindow()
        window.show()
        window.resize(1100, 700)
        process_events()

        chips = [
            button
            for button in window.library_view.findChildren(QPushButton)
            if button.property("role") == "quick-chip"
        ]
        assert chips, "Library toolbar should expose its filter chips"
        for chip in chips:
            assert chip.width() >= chip.sizeHint().width(), (
                f"Chip {chip.text()!r} is {chip.width()}px, "
                f"needs {chip.sizeHint().width()}px"
            )

        window.close()
        process_events()

    load_locale("ru")


def test_library_search_field_is_wide_enough_to_use(process_events):
    """Regression: the search field shared a row with three fixed-width
    buttons, so in a 280px Library pane it collapsed to about 50px and
    "Поиск по истории…" rendered as "Пои…". It has its own row now."""
    from core.i18n import load_locale, tr
    from PyQt6.QtGui import QFontMetrics
    from ui.main_window import MainWindow

    load_locale("ru")
    window = MainWindow()
    window.show()
    window.resize(1100, 700)
    process_events()
    process_events()

    field = window.library_view._search_edit
    placeholder = tr("history_search_placeholder")
    needed = QFontMetrics(field.font()).horizontalAdvance(placeholder)
    assert field.width() >= needed, (
        f"search field is {field.width()}px, placeholder needs {needed}px"
    )

    window.close()
    process_events()


def test_empty_state_hint_gets_the_height_its_wrapped_text_needs(process_events):
    """Regression: the hint was added with an alignment flag (and inside a
    layout with setAlignment), so it was given its sizeHint height —
    computed at a width Qt guessed, not the narrower one it gets in a
    280px Library pane — and the last line was cut off.

    Built directly rather than through MainWindow: the Library's empty
    state only shows while the shared history store happens to be empty.
    """
    from PyQt6.QtWidgets import QApplication

    from core.i18n import load_locale, tr
    from ui.empty_state import EmptyStateWidget
    from ui.theme import apply_theme

    load_locale("ru")
    # The stylesheet is load-bearing here: role="dim" sets the hint's font
    # size, and it is that font that pushes the text onto a third line the
    # unstyled sizeHint never accounted for.
    app = QApplication.instance()
    previous_sheet = app.styleSheet()
    apply_theme(app, "light")
    widget = EmptyStateWidget(
        "list", tr("library_empty_title"), tr("library_empty_hint")
    )
    # The Library pane's own width at its default 280px, less margins.
    widget.resize(256, 453)
    widget.show()
    process_events()
    process_events()

    hint = widget.hint_label
    assert hint is not None
    assert hint.isVisible()
    assert hint.height() >= hint.heightForWidth(hint.width()), (
        f"hint is {hint.height()}px tall, its text needs "
        f"{hint.heightForWidth(hint.width())}px at {hint.width()}px wide"
    )

    widget.close()
    app.setStyleSheet(previous_sheet)
    process_events()
