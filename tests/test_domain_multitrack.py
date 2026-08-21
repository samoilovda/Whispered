"""domain/multitrack.py must stay Qt-free, like the rest of domain/ (see
tests/test_domain_transcription.py), and its filename parser must behave as
documented in docs/MULTITRACK_ZOOM_PLAN.ru.md (M1)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from domain.multitrack import (
    MultiTrackRecording,
    detect_multitrack,
    parse_track_filename,
)


def test_domain_multitrack_does_not_import_qt_or_ui_or_live():
    src = Path("domain/multitrack.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_prefixes = ("PyQt6", "ui.", "ui", "core.live")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(forbidden_prefixes)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert not node.module.startswith(forbidden_prefixes)


@pytest.mark.parametrize(
    "filename,magic,expected",
    [
        ("audioDen1857894770.m4a", "857894770", ("Den", 1)),
        ("audioТонкиеМа2857894770.m4a", "857894770", ("ТонкиеМа", 2)),
        ("audioAlice9123.m4a", "123", ("Alice", 9)),
        ("audio5999.m4a", "999", ("", 5)),
    ],
)
def test_parse_track_filename_known_cases(filename, magic, expected):
    assert parse_track_filename(filename, magic) == expected


def test_parse_track_filename_missing_index_is_unresolvable():
    # "User" with no trailing digit after stripping prefix/magic: can't
    # separate a literal name from a missing index, so this is documented
    # as unparseable rather than guessed.
    assert parse_track_filename("audioUser123.m4a", "123") is None


def test_parse_track_filename_wrong_prefix_returns_none():
    assert parse_track_filename("notaudioDen1857894770.m4a", "857894770") is None


def test_parse_track_filename_without_magic_number_still_splits_index():
    assert parse_track_filename("audioDen1.m4a", "") == ("Den", 1)


@pytest.fixture()
def zoom_folder(tmp_path: Path) -> Path:
    root = tmp_path / "Кастдев Test"
    tracks = root / "Audio Record"
    tracks.mkdir(parents=True)
    magic = "111222333"
    (root / "recording.conf").write_text(
        json.dumps({
            "magic_number": magic,
            "items": [{"process": 100, "audio": f"audio{magic}.m4a", "prefix": "", "video": f"video{magic}.mp4"}],
        }),
        encoding="utf-8",
    )
    (root / f"audio{magic}.m4a").write_bytes(b"mix")
    (root / f"video{magic}.mp4").write_bytes(b"vid")
    (tracks / f"audioDen1{magic}.m4a").write_bytes(b"a")
    (tracks / f"audioТонкиеМа2{magic}.m4a").write_bytes(b"b")
    return root


def test_detect_multitrack_from_root_folder(zoom_folder: Path):
    rec = detect_multitrack(zoom_folder)
    assert isinstance(rec, MultiTrackRecording)
    assert rec.mixed_audio is not None and rec.mixed_audio.is_file()
    assert rec.video is not None and rec.video.is_file()
    assert rec.magic_number == "111222333"
    assert len(rec.tracks) == 2
    names = sorted((t.display_name, t.participant_index) for t in rec.tracks)
    assert names == [("Den", 1), ("ТонкиеМа", 2)]


def test_detect_multitrack_from_file_inside_tracks_dir(zoom_folder: Path):
    track_file = next((zoom_folder / "Audio Record").iterdir())
    rec = detect_multitrack(track_file)
    assert rec is not None
    assert rec.root == zoom_folder


def test_detect_multitrack_from_mixed_audio_file(zoom_folder: Path):
    rec = detect_multitrack(zoom_folder / "audio111222333.m4a")
    assert rec is not None
    assert rec.root == zoom_folder


def test_detect_multitrack_returns_none_without_conf(tmp_path: Path):
    plain = tmp_path / "regular_file.m4a"
    plain.write_bytes(b"x")
    assert detect_multitrack(plain) is None
    assert detect_multitrack(tmp_path) is None


def test_detect_multitrack_returns_none_without_tracks_dir(tmp_path: Path):
    (tmp_path / "recording.conf").write_text(json.dumps({"magic_number": "1", "items": []}))
    assert detect_multitrack(tmp_path) is None


def test_with_track_durations_fills_in_and_preserves_unmatched(zoom_folder: Path):
    rec = detect_multitrack(zoom_folder)
    assert rec is not None
    target = rec.tracks[0]
    updated = rec.with_track_durations({target.path: 42.5})
    updated_target = next(t for t in updated.tracks if t.path == target.path)
    other = next(t for t in updated.tracks if t.path != target.path)
    assert updated_target.duration == 42.5
    assert other.duration is None
    # Original is untouched (frozen dataclass, replace() copies).
    assert target.duration is None


def test_unparseable_track_falls_back_to_positional_name(tmp_path: Path):
    root = tmp_path / "rec"
    tracks = root / "Audio Record"
    tracks.mkdir(parents=True)
    magic = "5"
    (root / "recording.conf").write_text(json.dumps({"magic_number": magic, "items": []}))
    (tracks / "audioUser5.m4a").write_bytes(b"a")  # unresolvable per above
    rec = detect_multitrack(root)
    assert rec is not None
    assert len(rec.tracks) == 1
    assert rec.tracks[0].display_name == "Участник 1"
    assert rec.tracks[0].participant_index == 1
