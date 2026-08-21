"""domain/multitrack_merge.py: chronological merge into one
TranscriptionResult, and DoD from docs/MULTITRACK_ZOOM_PLAN.ru.md M4 that
every exporter still works on the result."""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

import exporters
from domain.multitrack_merge import TrackResult, merge_track_results
from domain.transcription import Segment


def test_domain_multitrack_merge_does_not_import_qt_or_ui_or_live():
    src = Path("domain/multitrack_merge.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_prefixes = ("PyQt6", "ui.", "ui", "core.live")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(forbidden_prefixes)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert not node.module.startswith(forbidden_prefixes)


def _tracks():
    den = TrackResult(
        source="track_1", display_name="Den", language="ru",
        segments=(
            Segment(start=0.0, end=2.0, text="привет всем"),
            Segment(start=5.0, end=6.0, text="ну давай начнём"),
        ),
    )
    roman = TrackResult(
        source="track_2", display_name="Роман", language="ru",
        segments=(
            Segment(start=2.5, end=4.0, text="привет, рад видеть"),
        ),
    )
    return den, roman


def test_merge_sorts_chronologically_and_assigns_speaker_ids():
    den, roman = _tracks()
    result = merge_track_results([den, roman], total_duration=120.0)
    assert [s.text for s in result.segments] == [
        "привет всем", "привет, рад видеть", "ну давай начнём",
    ]
    assert [s.speaker for s in result.segments] == ["track_1", "track_2", "track_1"]


def test_merge_populates_speaker_names_from_display_names():
    den, roman = _tracks()
    result = merge_track_results([den, roman], total_duration=120.0)
    assert result.speaker_names == {"track_1": "Den", "track_2": "Роман"}
    assert result.speaker_label("track_1") == "Den"


def test_merge_uses_total_duration_not_last_segment_end():
    den, roman = _tracks()
    result = merge_track_results([den, roman], total_duration=2305.0)
    assert result.duration == 2305.0


def test_merge_language_majority_vote():
    a = TrackResult(source="track_1", display_name="A", language="ru", segments=())
    b = TrackResult(source="track_2", display_name="B", language="ru", segments=())
    c = TrackResult(source="track_3", display_name="C", language="en", segments=())
    result = merge_track_results([a, b, c], total_duration=10.0)
    assert result.language == "ru"


def test_merge_preserves_overlapping_segments_from_different_tracks():
    a = TrackResult(
        source="track_1", display_name="A", language="ru",
        segments=(Segment(start=1.0, end=3.0, text="перебиваю"),),
    )
    b = TrackResult(
        source="track_2", display_name="B", language="ru",
        segments=(Segment(start=1.5, end=2.5, text="а я говорю дальше"),),
    )
    result = merge_track_results([a, b], total_duration=10.0)
    assert len(result.segments) == 2
    starts = sorted(s.start for s in result.segments)
    assert starts == [1.0, 1.5]


def test_merge_requires_at_least_one_track():
    with pytest.raises(ValueError):
        merge_track_results([], total_duration=10.0)


@pytest.mark.parametrize("export_fn,ext", [
    (exporters.export_txt_with_timestamps, "txt"),
    (exporters.export_srt, "srt"),
    (exporters.export_vtt, "vtt"),
    (exporters.export_json, "json"),
])
def test_merged_result_exports_with_speaker_names(export_fn, ext):
    den, roman = _tracks()
    result = merge_track_results([den, roman], total_duration=120.0)
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / f"out.{ext}")
        export_fn(result, path)
        content = Path(path).read_text(encoding="utf-8")
    assert "Den" in content
    assert "Роман" in content
