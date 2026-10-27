"""Vertical clipping and overlap on the start screen and transcript toolbar
(UI/UX audit 2026-10-18, UX-01 / T01).

The old gallery check compared widths only, so a 23px-tall "Choose file"
button (34px hint) and a drop-zone icon drawn over its title passed. These
tests use the gallery's stricter checks on the real widgets.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import render_ui_gallery as gallery  # noqa: E402
from core.i18n import load_locale  # noqa: E402


@pytest.fixture
def russian():
    load_locale("ru")
    yield
    load_locale("en")


@pytest.mark.parametrize("size", [(900, 550), (1100, 700), (1440, 900)])
def test_start_screen_neither_squeezes_nor_overlaps(russian, process_events, size):
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.resize(*size)
    for source in ("file", "recorder"):
        window.start_view.set_source(source)
        process_events()
        start = window.start_view
        assert gallery.vertically_clipped_widgets(start) == [], (source, size)
        assert gallery.overlapping_layout_siblings(start) == [], (source, size)
    window.start_view.set_source("file")
    process_events()
    browse = window.file_selector.browse_btn
    assert browse.height() >= browse.sizeHint().height() - 2
    window.close()
    process_events()


def test_drop_zone_title_does_not_run_into_the_icon(russian, process_events):
    from ui.file_selector import FileSelector

    selector = FileSelector()
    selector.resize(780, 400)
    selector.show()
    process_events()
    icon = selector.icon_label.geometry()
    title = selector.text_label.geometry()
    assert not icon.intersects(title)
    assert selector.drop_zone.height() >= selector.drop_zone.layout().minimumSize().height()
    selector.close()


def test_populated_transcript_toolbar_wraps_instead_of_clipping(russian, process_events):
    from domain.transcription import Segment, TranscriptionResult
    from ui.transcript_view import TranscriptView

    segments = [
        Segment(i * 5.0, i * 5.0 + 4.0, "Текст фрагмента", speaker=f"SPEAKER_0{i % 2}",
                confidence=0.4 if i in (1, 3) else 0.95)
        for i in range(6)
    ]
    view = TranscriptView()
    view.set_result(TranscriptionResult(segments=segments, language="ru", duration=30.0))
    for width in (780, 900):
        view.resize(width, 500)
        view.show()
        process_events()
        assert gallery.clipped_text_widgets(view) == [], width
        assert gallery.vertically_clipped_widgets(view) == [], width
        assert gallery.overlapping_layout_siblings(view) == [], width
    view.close()


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_settings_pages_scroll_instead_of_clipping_at_the_minimum_window(process_events, lang):
    from PyQt6.QtWidgets import QScrollArea

    from ui.settings_dialog import SettingsDialog

    load_locale(lang)
    try:
        dialog = SettingsDialog()
        dialog.show()
        dialog.resize(900, 550)
        process_events()
        assert dialog.minimumSizeHint().width() <= 900 and dialog.minimumSizeHint().height() <= 550
        for row in range(dialog._pages.count()):
            dialog._categories.setCurrentRow(row)
            process_events()
            area = dialog._pages.widget(row)
            assert isinstance(area, QScrollArea)
            inner = area.widget()
            # Wide enough for its content (no sideways clipping) …
            assert inner.minimumSizeHint().width() <= area.viewport().width(), (lang, row)
            # … and when it is taller than the window, the user can scroll to the rest.
            if inner.minimumSizeHint().height() > area.viewport().height():
                assert area.verticalScrollBar().maximum() > 0, (lang, row)
        dialog.close()
    finally:
        load_locale("en")


def test_clips_form_keeps_its_widgets_whole_at_the_minimum_window(russian, process_events, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QComboBox, QLineEdit, QPushButton

    import config
    from core.history import HistoryStore
    from domain.transcription import Segment, TranscriptionResult
    from ui.main_window import MainWindow

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    store = HistoryStore(db_path=tmp_path / "h.sqlite3")
    monkeypatch.setattr("core.history.get_history_store", lambda: store)
    rid = store.add(TranscriptionResult(segments=[Segment(0.0, 1.0, "hi")], language="ru", duration=1.0),
                    source_path="", source_name="a.mp3")
    window = MainWindow()
    window.show()
    window.resize(900, 550)
    window._open_record_view(rid)
    window._show_tab(window.clips_view)
    process_events()
    clips = window.clips_view
    squeezed = [
        (type(w).__name__, w.height(), w.sizeHint().height())
        for w in clips.findChildren((QLineEdit, QComboBox, QPushButton))
        if w.isVisibleTo(clips) and w.height() < w.sizeHint().height() - 1
        and not w.objectName().startswith("qt_")
    ]
    assert squeezed == []
    # The actions under the scrolling form are not part of it.
    assert clips.export_btn.height() >= clips.export_btn.sizeHint().height() - 1
    window._revision_save_timer.stop()
    window.close()
    process_events()
