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


def test_recipe_editor_default_name_nudges_toward_a_copy(process_events):
    """Regression basis for docs/IMPROVEMENT_PLAN_2026-08.ru.md, A4: the
    name field must default to something other than the built-in's own
    display name, so saving unchanged doesn't read as overwriting it."""
    from core.i18n import load_locale, tr
    from domain.recipe import PODCAST_ARTICLE
    from ui.recipe_editor import RecipeEditorDialog
    from ui.transcribe_options import TranscribeOptionsPopover

    load_locale("en")
    options = TranscribeOptionsPopover(embedded=True)
    dialog = RecipeEditorDialog(options, PODCAST_ARTICLE)
    process_events()

    assert dialog.recipe_name() != tr("recipe_podcast_article")
    assert tr("recipe_podcast_article") in dialog.recipe_name()

    options.setParent(None)
    dialog.close()


def test_recipe_editor_save_without_changes_does_not_touch_config(
    monkeypatch, tmp_path, process_events,
):
    """Regression: "Save" with no actual change used to overwrite
    Config.recipes with an unnamed "custom" slot and switch
    Config.last_recipe to it, silently discarding the built-in the editor
    was opened on and unchecking every start-screen chip."""
    import config
    from PyQt6.QtWidgets import QDialog
    from ui.main_window import MainWindow
    from ui.recipe_editor import RecipeEditorDialog

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config(last_recipe="podcast_article"))

    def fake_exec(_dialog):
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RecipeEditorDialog, "exec", fake_exec)

    window = MainWindow()
    window.show()
    window.start_view.select_recipe("podcast_article")
    process_events()

    window._open_recipe_editor()
    process_events()

    assert config.get_config().recipes == []
    assert config.get_config().last_recipe == "podcast_article"
    assert window.start_view.current_recipe_key() == "podcast_article"
    assert window.start_view._recipe_buttons["podcast_article"].isChecked()

    window.close()
    process_events()


def test_recipe_editor_save_with_changes_names_the_new_recipe(
    monkeypatch, tmp_path, process_events,
):
    """A real step-selection change is saved as a named custom recipe —
    the name field's value ends up on Config.recipes, not a hardcoded
    "custom" label."""
    import config
    from PyQt6.QtWidgets import QDialog
    from ui.main_window import MainWindow
    from ui.recipe_editor import RecipeEditorDialog

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config(last_recipe="podcast_article"))

    def fake_exec(dialog):
        dialog._checks["article"].setChecked(False)
        dialog._name_edit.setText("My trimmed recipe")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(RecipeEditorDialog, "exec", fake_exec)

    window = MainWindow()
    window.show()
    window.start_view.select_recipe("podcast_article")
    process_events()

    window._open_recipe_editor()
    process_events()

    assert len(config.get_config().recipes) == 1
    saved = config.get_config().recipes[0]
    assert saved["name"] == "My trimmed recipe"
    assert saved["steps"] == ["transcribe", "clean"]
    assert config.get_config().last_recipe == "custom"
    assert window.start_view.current_recipe_key() == "custom"

    window.close()
    process_events()


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


def test_live_setup_panel_is_never_squeezed_below_its_own_minimum(process_events):
    """Regression: the Live source page stacks session setup, preflight
    and diagnostics above the session controls, and everything else on the
    start screen took its share of the height first. The setup panel was
    left about 114px for a layout whose own minimum is 274 — and a
    QVBoxLayout given less than its minimum does not clip, it overlaps its
    children, drawing the device combo straight over the
    Microphone/Meeting-audio checkboxes. The page scrolls now.
    """
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.resize(1100, 700)
    process_events()

    window.start_view.set_source("live")
    process_events()
    process_events()

    panel = window.live_view.options_panel.findChild(_live_setup_panel_type())
    assert panel is not None
    assert panel.height() >= panel.body_layout.minimumSize().height(), (
        f"setup panel is {panel.height()}px for a layout needing "
        f"{panel.body_layout.minimumSize().height()}px — its rows overlap"
    )
    # And the rows really are laid out one under the next.
    tops = [
        widget.mapTo(panel, widget.rect().topLeft()).y()
        for widget in (
            panel.mic_check, panel.mic_combo, panel.model_combo, panel.language_combo,
        )
    ]
    assert tops == sorted(tops)
    assert panel.mic_combo.mapTo(panel, panel.mic_combo.rect().topLeft()).y() >= (
        panel.mic_check.mapTo(panel, panel.mic_check.rect().bottomLeft()).y()
    )

    window.close()
    process_events()


def test_a_finished_recording_can_actually_be_launched(process_events, tmp_path):
    """Regression: the Launch button was shown for the "file" source only,
    so after recording something the start screen armed the clip, enabled
    the button — and kept it hidden. The recorder page has no transcribe
    button of its own, leaving no way to transcribe what was just
    recorded short of guessing that switching back to "File" would keep
    the clip."""
    from ui.main_window import MainWindow

    clip = tmp_path / "REC.wav"
    clip.write_bytes(b"RIFF0000WAVE" + b"\0" * 64)

    window = MainWindow()
    window.show()
    window.resize(1200, 800)
    window.start_view.set_source("recorder")
    process_events()

    window._on_recording_ready(str(clip))
    process_events()

    assert window.file_selector.get_file() == str(clip)
    assert window.transcribe_btn.isEnabled()
    assert window.transcribe_btn.isVisible()

    # Live and the folder queue still drive themselves.
    window.start_view.set_source("live")
    process_events()
    assert not window.transcribe_btn.isVisible()
    window.start_view.set_source("folder")
    process_events()
    assert not window.transcribe_btn.isVisible()

    window.close()
    process_events()


def _live_setup_panel_type():
    from ui.live_setup_panel import LiveSetupPanel

    return LiveSetupPanel


def test_folder_source_hides_the_recipe_picker_it_does_not_honour(process_events):
    """Regression basis for docs/IMPROVEMENT_PLAN_2026-08.ru.md, A6
    (minimal variant): the folder queue only transcribes each item
    (MainWindow._on_batch_item_finished), but the recipe chips stayed
    visible for that source, promising a choice the queue doesn't act on.
    """
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()

    window.start_view.set_source("file")
    process_events()
    assert window.start_view._recipe_row_widget.isVisibleTo(window.start_view)
    assert window.start_view._recipe_label.isVisibleTo(window.start_view)

    window.start_view.set_source("folder")
    process_events()
    assert not window.start_view._recipe_row_widget.isVisibleTo(window.start_view)
    assert not window.start_view._recipe_label.isVisibleTo(window.start_view)
    assert not window.start_view._summary_row_widget.isVisibleTo(window.start_view)

    window.start_view.set_source("file")
    process_events()
    assert window.start_view._recipe_row_widget.isVisibleTo(window.start_view)

    window.close()
    process_events()

