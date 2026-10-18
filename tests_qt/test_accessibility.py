"""Text scale, accessible names, keyboard reach (UI/UX audit 2026-10-18,
UX-12 / T12b). Screen readers (VoiceOver, NVDA) are not run here — see the
status journal."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton, QSlider, QToolButton

import config
from core.i18n import load_locale
from transcriber import Segment, TranscriptionResult
from ui import theme

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import render_ui_gallery as gallery  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_theme(qt_application):
    """The suite runs without the app stylesheet; put back exactly that
    (and the theme module's own state) so later tests are unaffected."""
    sheet, name, scale = qt_application.styleSheet(), theme.get_theme().name, theme.get_text_scale()
    yield
    theme.apply_theme(qt_application, name, scale)
    qt_application.setStyleSheet(sheet)


def test_scale_multiplies_theme_and_widget_font_sizes(qt_application):
    base = int(theme.DARK.font_md[:-2])
    theme.apply_theme(qt_application, "dark", 200)
    assert theme.get_text_scale() == 200
    assert theme.get_theme().font_md == f"{base * 2}px"
    assert theme.px(11) == 22
    theme.apply_theme(qt_application, "dark", 100)
    assert theme.px(11) == 11 and theme.get_theme().font_md == theme.DARK.font_md
    theme.apply_theme(qt_application, "dark", 137)  # not an offered scale
    assert theme.get_text_scale() == 100


def test_invalid_text_scale_is_reported_by_config_validation():
    assert any("text_scale" in w for w in config.Config(text_scale=90).validate())
    assert not config.Config(text_scale=125).validate()


def test_settings_preview_save_and_cancel(monkeypatch, tmp_path, process_events):
    from ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    dialog = SettingsDialog()
    combo = dialog._scale_combo
    combo.setCurrentIndex(combo.findData(150))
    assert theme.get_text_scale() == 150          # previewed at once
    assert config.get_config().text_scale == 100  # not saved yet
    dialog.reject()
    assert theme.get_text_scale() == 100          # Cancel puts it back
    dialog = SettingsDialog()
    dialog._scale_combo.setCurrentIndex(dialog._scale_combo.findData(125))
    dialog._on_apply()
    assert config.get_config().text_scale == 125
    dialog._scale_combo.setCurrentIndex(dialog._scale_combo.findData(100))  # reset
    dialog._on_apply()
    assert config.get_config().text_scale == 100
    dialog.close()


@pytest.mark.parametrize("scale", [125, 150, 200])
def test_nothing_is_clipped_or_overlapping_at_large_text(scale, qt_application, process_events):
    from ui.main_window import MainWindow

    load_locale("ru")
    theme.apply_theme(qt_application, "dark", scale)
    window = MainWindow()
    window.show()
    window.resize(1100, 700)
    process_events()
    try:
        for source in ("file", "recorder"):
            window.start_view.set_source(source)
            process_events()
            # The Launch button stays reachable: the screen scrolls to it.
            assert gallery.clipped_text_widgets(window.start_view) == [], (scale, source)
            assert gallery.vertically_clipped_widgets(window.start_view) == [], (scale, source)
        window._document_session.apply_result(TranscriptionResult(
            segments=[Segment(0.0, 5.0, "Текст", speaker="A", confidence=0.4)],
            language="ru", duration=5.0))
        window._stack.setCurrentIndex(window._record_index)
        process_events()
        assert gallery.vertically_clipped_widgets(window.transcript_view) == []
        assert gallery.overlapping_layout_siblings(window.transcript_view) == []
    finally:
        window.close()
        load_locale("en")
        process_events()


def test_every_visible_control_has_an_accessible_name(process_events):
    from ui.main_window import MainWindow

    load_locale("en")
    window = MainWindow()
    window.show()
    window._document_session.apply_result(TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hi")], language="en", duration=1.0))
    process_events()
    unnamed = []
    try:
        for index in range(window._stack.count()):
            window._stack.setCurrentIndex(index)
            process_events()
            for tab in range(window.main_tabs.count()):
                window.main_tabs.setCurrentIndex(tab)
                process_events()
                for cls in (QPushButton, QToolButton, QLineEdit, QComboBox, QSlider):
                    for widget in window.findChildren(cls):
                        if not widget.isVisibleTo(window) or widget.accessibleName():
                            continue
                        # Qt's own parts: a spin box's inner line edit, a
                        # line edit's clear button.
                        if widget.objectName().startswith("qt_") or isinstance(
                                widget.parent(), QLineEdit):
                            continue
                        text = widget.text() if cls in (QPushButton, QToolButton) else ""
                        if cls is QComboBox:
                            text = widget.currentText()
                        placeholder = widget.placeholderText() if cls is QLineEdit else ""
                        if not (text.strip() or placeholder):
                            unnamed.append((index, tab, cls.__name__, widget.toolTip()[:30], widget.parent().metaObject().className()))
    finally:
        window.close()
        process_events()
    assert unnamed == []


def test_chat_citation_is_a_keyboard_link_and_only_linked_bubbles_take_focus(process_events):
    from ui.chat_panel import ChatPanel

    panel = ChatPanel()
    segs = [Segment(0.0, 60.0, "text " * 20)]
    panel.set_record_context(None, "r", " ".join(s.text for s in segs), segs, persist=False)
    linked = panel._add_bubble("See [00:30].", "assistant")
    plain = panel._add_bubble("No times here.", "assistant")
    user = panel._add_bubble("question", "user")
    flags = linked.textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.LinksAccessibleByKeyboard
    assert linked.focusPolicy() == Qt.FocusPolicy.TabFocus
    assert plain.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert user.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_playback_keys_do_not_steal_typing(monkeypatch, process_events):
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()
    window._document_session.apply_result(TranscriptionResult(
        segments=[Segment(0.0, 1.0, "hi")], language="en", duration=1.0))
    window.transcript_view.set_result(window._document_session.current_result)
    window._stack.setCurrentIndex(window._record_index)
    editor = window.transcript_view.text_edit
    # Offscreen windows are never "active", so name the focused widget.
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: editor))
    assert not window._focus_is_editable_text()   # reading mode: keys play/seek
    window.transcript_view._enter_edit_mode()
    assert window._focus_is_editable_text()       # editing: keys are typing
    window.transcript_view._exit_edit_mode(save=False)
    window.close()
    process_events()
