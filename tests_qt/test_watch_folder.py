"""Real-Qt tests for core/watch_folder.py's WatchFolderService (see
docs/IMPROVEMENT_PLAN_2026-08.ru.md, B5b).

The real debounce waits _DEBOUNCE_MS (2s) on a QTimer before confirming a
file's size is stable — these tests call _recheck() directly instead of
waiting on the real timer, simulating "the debounce tick fired" without
actually sleeping.
"""

from __future__ import annotations

from core.watch_folder import WatchFolderService


def test_new_file_is_reported_once_its_size_is_stable(tmp_path, process_events):
    folder = tmp_path
    clip = folder / "clip.mp3"
    clip.write_bytes(b"\0" * 32)

    service = WatchFolderService()
    found = []
    service.file_found.connect(found.append)

    service.set_folder(str(folder))
    process_events()
    assert found == [], "must not report before a stable-size recheck"

    service._recheck()
    process_events()
    assert found == [str(clip)]


def test_a_growing_file_is_not_reported_until_it_stops_changing(tmp_path, process_events):
    folder = tmp_path
    clip = folder / "recording.mp3"
    clip.write_bytes(b"\0" * 10)

    service = WatchFolderService()
    found = []
    service.file_found.connect(found.append)
    service.set_folder(str(folder))
    process_events()

    # Still being written between the first sighting and the recheck.
    clip.write_bytes(b"\0" * 20)
    service._recheck()
    process_events()
    assert found == []

    # Now it stops growing — the next two identical-size rechecks confirm it.
    service._recheck()
    process_events()
    assert found == [str(clip)]


def test_a_copy_under_a_different_name_is_not_reported_twice(tmp_path, process_events):
    folder = tmp_path
    original = folder / "session.mp3"
    original.write_bytes(b"identical audio content")

    service = WatchFolderService()
    found = []
    service.file_found.connect(found.append)
    service.set_folder(str(folder))
    process_events()
    service._recheck()
    process_events()
    assert found == [str(original)]

    copy = folder / "session (copy).mp3"
    copy.write_bytes(b"identical audio content")
    service._recheck()
    process_events()
    service._recheck()
    process_events()

    assert found == [str(original)], "a byte-identical copy must not be re-reported"


def test_unsupported_files_are_ignored(tmp_path, process_events):
    folder = tmp_path
    (folder / "notes.txt").write_text("not audio")

    service = WatchFolderService()
    found = []
    service.file_found.connect(found.append)
    service.set_folder(str(folder))
    process_events()
    service._recheck()
    process_events()

    assert found == []


def test_set_folder_to_empty_string_stops_watching(tmp_path, process_events):
    folder = tmp_path
    clip = folder / "clip.mp3"
    clip.write_bytes(b"\0" * 32)

    service = WatchFolderService()
    found = []
    service.file_found.connect(found.append)
    service.set_folder(str(folder))
    process_events()

    service.set_folder("")
    process_events()
    assert service._watcher.directories() == []

    # A recheck after stopping must not resurrect the old folder's state.
    service._recheck()
    process_events()
    assert found == []
