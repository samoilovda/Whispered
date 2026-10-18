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
