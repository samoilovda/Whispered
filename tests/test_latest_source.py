"""Unit tests for domain/latest_source.py — newest file/folder pick."""

from __future__ import annotations

import os

from domain.latest_source import find_latest_media, pick_media

EXT = {".m4a", ".mp4", ".mp3"}


def _touch(path, mtime, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def test_missing_or_empty_root_returns_none(tmp_path):
    assert find_latest_media(tmp_path / "nope", EXT) is None
    assert find_latest_media(tmp_path, EXT) is None


def test_newest_loose_file_wins_and_unsupported_ignored(tmp_path):
    _touch(tmp_path / "old.mp3", 100)
    new = _touch(tmp_path / "new.mp3", 200)
    _touch(tmp_path / "notes.txt", 300)
    assert find_latest_media(tmp_path, EXT) == new


def test_newest_folder_prefers_audio_only(tmp_path):
    _touch(tmp_path / "loose.mp3", 100)
    _touch(tmp_path / "meeting" / "video1.mp4", 200, b"v" * 1000)
    mix = _touch(tmp_path / "meeting" / "audio_only.m4a", 200, b"a")
    assert find_latest_media(tmp_path, EXT) == mix


def test_folder_freshness_uses_its_files(tmp_path):
    old_dir = tmp_path / "a"
    new_file = _touch(old_dir / "rec.m4a", 500)
    os.utime(old_dir, (50, 50))
    _touch(tmp_path / "b" / "rec.m4a", 100)
    os.utime(tmp_path / "b", (100, 100))
    assert find_latest_media(tmp_path, EXT) == new_file


def test_folder_without_media_is_skipped(tmp_path):
    _touch(tmp_path / "empty" / "readme.txt", 900)
    fallback = _touch(tmp_path / "file.mp3", 100)
    assert find_latest_media(tmp_path, EXT) == fallback


def test_pick_media_falls_back_to_largest(tmp_path):
    _touch(tmp_path / "a.mp4", 1, b"1")
    big = _touch(tmp_path / "b.mp4", 1, b"123")
    assert pick_media(tmp_path, {".mp4"}) == big


def test_source_folder_survives_restart(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    cfg = config.Config()
    cfg.source_folder = str(tmp_path / "Zoom")
    assert cfg.save()

    assert config.Config.load().source_folder == str(tmp_path / "Zoom")


def test_companion_video_for_zoom_audio_only(tmp_path):
    from application.youtube_publish import find_video_source

    audio = _touch(tmp_path / "rec" / "audio_only.m4a", 1)
    _touch(tmp_path / "rec" / "gallery.mp4", 1, b"1")
    video = _touch(tmp_path / "rec" / "video123.mp4", 1, b"123")
    assert find_video_source(audio) == video
    assert find_video_source(video) == video


def test_companion_video_same_name_and_unrelated_audio(tmp_path):
    from application.youtube_publish import find_video_source

    talk = _touch(tmp_path / "talk.mp3", 1)
    clip = _touch(tmp_path / "talk.mp4", 1)
    other = _touch(tmp_path / "other.mp3", 1)
    assert find_video_source(talk) == clip
    assert find_video_source(other) is None
