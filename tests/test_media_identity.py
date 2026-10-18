"""core.media_identity and HistoryStore.relink_media (UX-09 / T09)."""

from __future__ import annotations

from types import SimpleNamespace

from core.history import HistoryStore
from core.media_identity import (
    DIFFERENT, LIKELY, SAME, UNKNOWN, compare_media, media_fingerprint,
)


def _write(path, head: bytes, size: int = 3 << 20):
    path.write_bytes(head + b"\0" * (size - len(head)))
    return path


def test_renamed_copy_has_the_same_fingerprint(tmp_path):
    a = _write(tmp_path / "a.mp4", b"interview")
    b = tmp_path / "renamed.mp4"
    b.write_bytes(a.read_bytes())
    assert media_fingerprint(str(a)) == media_fingerprint(str(b))


def test_same_size_different_content_differs(tmp_path):
    a = _write(tmp_path / "a.mp4", b"interview one")
    b = _write(tmp_path / "b.mp4", b"interview two")
    assert media_fingerprint(str(a)) != media_fingerprint(str(b))


def test_compare_media():
    assert compare_media("v1:x", 60.0, "v1:x", 60.0) == SAME
    assert compare_media("v1:x", 60.0, "v1:y", 60.0) == DIFFERENT  # same length, other video
    assert compare_media("", 60.0, "v1:y", 60.5) == LIKELY
    assert compare_media("", 60.0, "v1:y", 75.0) == DIFFERENT
    assert compare_media("", 60.0, "v1:y", None) == UNKNOWN


def test_relink_keeps_source_path_and_stamps_only_a_replacement(tmp_path):
    store = HistoryStore(db_path=tmp_path / "h.db")
    result = SimpleNamespace(segments=[], language="en", duration=1.0)
    rid = store.add(result, source_path="/old/interview.mp4")
    store.relink_media(rid, "/new/renamed.mp4", "v1:1:aa")
    record = store.get_record(rid)
    assert record["source_path"] == "/old/interview.mp4"
    assert record["media_path"] == "/new/renamed.mp4" and record["media_changed_at"] == ""
    store.relink_media(rid, "/new/other.mp4", "v1:1:bb", replaced=True)
    assert store.get_record(rid)["media_changed_at"]
    store.set_media_fingerprint(rid, "v1:1:cc")  # never overwrites an existing one
    assert store.get_record(rid)["media_fingerprint"] == "v1:1:bb"


def test_results_made_before_a_replacement_read_as_stale(tmp_path):
    import dataclasses

    from application.media_relink import made_before
    from application.steps import STEP_REGISTRY, build_step_context
    from domain.transcription import Segment, TranscriptionResult
    from infrastructure.persistence import artifact_store

    result = TranscriptionResult(segments=[Segment(0.0, 1.0, "hi")], language="en", duration=1.0)
    context = build_step_context("/m/a.mp4", result, 7, out_dir=tmp_path)
    artifact = STEP_REGISTRY["article"].make_artifact(context)
    artifact_store.save(dataclasses.replace(artifact, created_at="2026-10-01T10:00:00+00:00"))
    assert made_before(context, "article", "2026-10-02T10:00:00+00:00")
    assert not made_before(context, "article", "2026-09-30T10:00:00+00:00")
    assert not made_before(context, "article", "")
    assert not made_before(context, "insights", "2026-10-02T10:00:00+00:00")  # nothing saved
