"""Unit tests for domain/watch_folder.py (see
docs/IMPROVEMENT_PLAN_2026-08.ru.md, B5b). Qt-free — no watcher/timer
involved, just the "what's new" filter core/watch_folder.py's debounce
calls into.
"""

from __future__ import annotations

from domain.watch_folder import content_fingerprint, new_files


def test_new_files_returns_everything_when_nothing_is_seen_yet(tmp_path):
    a = tmp_path / "a.mp3"
    a.write_bytes(b"audio-a")
    b = tmp_path / "b.mp3"
    b.write_bytes(b"audio-b")

    result = new_files(set(), [a, b])

    assert set(result) == {a, b}


def test_new_files_filters_out_an_already_seen_fingerprint(tmp_path):
    a = tmp_path / "a.mp3"
    a.write_bytes(b"audio-a")
    b = tmp_path / "b.mp3"
    b.write_bytes(b"audio-b")
    seen = {content_fingerprint(a)}

    result = new_files(seen, [a, b])

    assert result == [b]


def test_a_copy_under_a_different_name_is_not_new(tmp_path):
    """Acceptance criterion: dropping the same file again under a
    different name must not appear as new — content_fingerprint() is
    path-independent (unlike application.artifact_provenance's
    source_fingerprint, which bakes the path in by design and would
    treat a rename/copy as a brand-new file)."""
    original = tmp_path / "session.mp3"
    original.write_bytes(b"identical audio content")
    seen = {content_fingerprint(original)}

    copy = tmp_path / "session (copy).mp3"
    copy.write_bytes(b"identical audio content")

    result = new_files(seen, [copy])

    assert result == []


def test_different_files_with_the_same_size_are_not_deduped_by_size_alone(tmp_path):
    a = tmp_path / "a.mp3"
    a.write_bytes(b"AAAAAAAA")
    b = tmp_path / "b.mp3"
    b.write_bytes(b"BBBBBBBB")
    seen = {content_fingerprint(a)}

    result = new_files(seen, [b])

    assert result == [b]


def test_content_fingerprint_is_stable_for_the_same_file(tmp_path):
    path = tmp_path / "clip.mp3"
    path.write_bytes(b"some bytes")
    assert content_fingerprint(path) == content_fingerprint(path)


def test_content_fingerprint_of_an_unreadable_path_does_not_raise(tmp_path):
    missing = tmp_path / "does-not-exist.mp3"
    fingerprint = content_fingerprint(missing)
    assert isinstance(fingerprint, str)
    assert fingerprint
