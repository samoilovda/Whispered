"""Unit tests for domain/recording_settle.py — when a new recording in the
source folder (Zoom's) counts as finished. Qt-free: no watcher or timer,
just the snapshots and the settle state machine core/recording_watch.py
calls into.
"""

from __future__ import annotations

from domain.recording_settle import (
    SettleTracker, default_recording_folder, entry_snapshot, media_for, scan,
)

EXT = {".m4a", ".mp4", ".mp3"}


def _meeting(root, name="2026-10-27 10.00.00 Meeting"):
    folder = root / name
    folder.mkdir()
    return folder


def test_a_zoom_folder_still_converting_is_not_finished(tmp_path):
    folder = _meeting(tmp_path)
    (folder / "double_click_to_convert_01.zoom").write_bytes(b"raw")
    (folder / "audio_only.m4a").write_bytes(b"partial")

    assert entry_snapshot(folder, EXT) is None


def test_a_converted_zoom_folder_is_finished(tmp_path):
    folder = _meeting(tmp_path)
    (folder / "audio_only.m4a").write_bytes(b"audio")
    (folder / "video1234.mp4").write_bytes(b"video")

    assert entry_snapshot(folder, EXT) is not None
    assert media_for(folder, EXT) == folder / "audio_only.m4a"


def test_an_empty_media_file_is_not_finished(tmp_path):
    folder = _meeting(tmp_path)
    (folder / "video1234.mp4").write_bytes(b"")

    assert entry_snapshot(folder, EXT) is None


def test_a_loose_media_file_is_its_own_media(tmp_path):
    clip = tmp_path / "call.mp3"
    clip.write_bytes(b"audio")

    assert entry_snapshot(clip, EXT) is not None
    assert media_for(clip, EXT) == clip
    assert entry_snapshot(tmp_path / "notes.txt", EXT) is None


def test_recordings_finished_before_the_baseline_are_never_reported(tmp_path):
    folder = _meeting(tmp_path)
    (folder / "audio_only.m4a").write_bytes(b"audio")
    tracker = SettleTracker(settle_seconds=10)
    tracker.baseline(scan(tmp_path, EXT))

    assert tracker.observe(scan(tmp_path, EXT), now=0) == []
    assert tracker.observe(scan(tmp_path, EXT), now=100) == []
    assert not tracker.is_settling()


def test_a_new_recording_is_reported_once_after_settling(tmp_path):
    tracker = SettleTracker(settle_seconds=10)
    tracker.baseline(scan(tmp_path, EXT))
    folder = _meeting(tmp_path)
    (folder / "audio_only.m4a").write_bytes(b"audio")

    assert tracker.observe(scan(tmp_path, EXT), now=0) == []
    assert tracker.is_settling()
    assert tracker.observe(scan(tmp_path, EXT), now=5) == []
    assert tracker.observe(scan(tmp_path, EXT), now=10) == [str(folder)]
    assert tracker.observe(scan(tmp_path, EXT), now=50) == []


def test_a_recording_that_keeps_growing_restarts_the_settle_window(tmp_path):
    tracker = SettleTracker(settle_seconds=10)
    tracker.baseline(scan(tmp_path, EXT))
    folder = _meeting(tmp_path)
    media = folder / "audio_only.m4a"
    media.write_bytes(b"a")
    tracker.observe(scan(tmp_path, EXT), now=0)

    media.write_bytes(b"a-longer-file")
    assert tracker.observe(scan(tmp_path, EXT), now=10) == []
    assert tracker.observe(scan(tmp_path, EXT), now=20) == [str(folder)]


def test_a_meeting_converting_at_startup_is_offered_once_done(tmp_path):
    """The app started mid-conversion: the folder was unfinished at the
    baseline, so it is not treated as an old recording."""
    folder = _meeting(tmp_path)
    raw = folder / "double_click_to_convert_01.zoom"
    raw.write_bytes(b"raw")
    tracker = SettleTracker(settle_seconds=10)
    tracker.baseline(scan(tmp_path, EXT))
    assert tracker.unfinished(scan(tmp_path, EXT)) == [str(folder)]

    raw.unlink()
    (folder / "audio_only.m4a").write_bytes(b"audio")
    tracker.observe(scan(tmp_path, EXT), now=0)

    assert tracker.observe(scan(tmp_path, EXT), now=15) == [str(folder)]
    assert tracker.unfinished(scan(tmp_path, EXT)) == []


def test_a_missing_folder_scans_empty(tmp_path):
    assert scan(tmp_path / "nope", EXT) == {}


def test_the_default_folder_is_zooms(tmp_path):
    assert default_recording_folder().endswith("Zoom")
