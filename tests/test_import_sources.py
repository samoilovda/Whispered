"""application.import_sources (UI/UX audit 2026-10-18, UX-03 / T03)."""

from __future__ import annotations

import os
import sys

import pytest

from application.import_sources import collect_import_sources, is_droppable


def _supported(path: str) -> bool:
    return path.endswith((".mp3", ".wav"))


def _touch(path):
    path.write_bytes(b"\0")
    return path


def test_two_files_stay_two_in_drop_order(tmp_path):
    a, b = _touch(tmp_path / "b.mp3"), _touch(tmp_path / "a.wav")
    batch = collect_import_sources([str(a), str(b)], _supported)
    assert batch.files == [str(a), str(b)]
    assert batch.skipped == 0


def test_unsupported_first_url_does_not_lose_the_rest(tmp_path):
    txt, clip = _touch(tmp_path / "notes.txt"), _touch(tmp_path / "clip.mp3")
    batch = collect_import_sources([str(txt), str(clip)], _supported)
    assert batch.files == [str(clip)]
    assert batch.unsupported == 1


def test_folder_expands_one_level_sorted_and_counts_unsupported(tmp_path):
    folder = tmp_path / "session"
    folder.mkdir()
    _touch(folder / "two.wav")
    _touch(folder / "one.mp3")
    _touch(folder / "readme.txt")
    (folder / "nested").mkdir()
    _touch(folder / "nested" / "deep.mp3")
    batch = collect_import_sources([str(folder)], _supported)
    assert batch.files == [str(folder / "one.mp3"), str(folder / "two.wav")]
    assert batch.unsupported == 1


def test_duplicates_direct_and_via_folder_are_kept_once(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    clip = _touch(folder / "clip.mp3")
    batch = collect_import_sources([str(clip), str(folder), str(clip)], _supported)
    assert batch.files == [str(clip)]
    assert batch.duplicates == 2


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="needs POSIX permissions and a non-root user")
def test_unreadable_folder_does_not_cancel_other_sources(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    _touch(locked / "hidden.mp3")
    clip = _touch(tmp_path / "ok.mp3")
    locked.chmod(0)
    try:
        batch = collect_import_sources([str(locked), str(clip)], _supported)
    finally:
        locked.chmod(0o755)
    assert batch.files == [str(clip)]
    assert batch.unreadable == [str(locked)]


def test_non_local_count_is_reported(tmp_path):
    batch = collect_import_sources([], _supported, non_local=2)
    assert batch.files == [] and batch.non_local == 2 and batch.skipped == 2


def test_is_droppable(tmp_path):
    assert is_droppable(str(tmp_path), _supported)
    assert is_droppable(str(tmp_path / "x.mp3"), _supported)
    assert not is_droppable(str(tmp_path / "x.txt"), _supported)
