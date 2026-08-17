"""Unit tests for domain/artifact.py and
infrastructure/persistence/artifact_store.py (R5-full/R8-pre, see
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md).
"""

from __future__ import annotations

from domain.artifact import Artifact
from infrastructure.persistence import artifact_store


def _artifact(path: str, **overrides) -> Artifact:
    fields = dict(
        record_id="42",
        source_hash="abc123",
        source_path="/media/interview.mp4",
        transcript_revision="rev-1",
        type="cover",
        path=path,
        provider="lmstudio",
        model="gemma-4-12b",
        prompt_version="v3",
    )
    fields.update(overrides)
    return Artifact(**fields)


def test_to_dict_from_dict_roundtrips():
    original = _artifact("/out/cover.png", extra={"variant": "16x9"})
    restored = Artifact.from_dict(original.to_dict())
    assert restored == original


def test_from_dict_defaults_missing_optional_fields():
    minimal = {
        "record_id": 1,
        "source_hash": "h",
        "source_path": "/x.mp4",
        "transcript_revision": "r1",
        "type": "cover",
        "path": "/out/cover.png",
    }
    artifact = Artifact.from_dict(minimal)
    assert artifact.provider == ""
    assert artifact.model == ""
    assert artifact.prompt_version == ""
    assert artifact.extra == {}
    assert artifact.created_at  # non-empty, auto-filled


def test_cache_key_equal_for_same_inputs_different_timestamps():
    a = _artifact("/out/a.png")
    b = _artifact("/out/b.png", created_at="2020-01-01T00:00:00+00:00")
    assert a.cache_key() == b.cache_key()


def test_cache_key_differs_when_prompt_version_changes():
    a = _artifact("/out/a.png", prompt_version="v3")
    b = _artifact("/out/a.png", prompt_version="v4")
    assert a.cache_key() != b.cache_key()


def test_cache_key_differs_when_transcript_revision_changes():
    a = _artifact("/out/a.png", transcript_revision="rev-1")
    b = _artifact("/out/a.png", transcript_revision="rev-2")
    assert a.cache_key() != b.cache_key()


# ---------------------------------------------------------------------------
# artifact_store
# ---------------------------------------------------------------------------

def test_save_then_load_roundtrips(tmp_path):
    target = tmp_path / "cover.png"
    target.write_bytes(b"fake png")
    artifact = _artifact(str(target))

    manifest_path = artifact_store.save(artifact)

    assert manifest_path == artifact_store.manifest_path_for(target)
    assert manifest_path.exists()
    loaded = artifact_store.load(target)
    assert loaded == artifact


def test_load_returns_none_when_manifest_missing(tmp_path):
    assert artifact_store.load(tmp_path / "nope.png") is None


def test_load_returns_none_for_corrupt_manifest(tmp_path):
    target = tmp_path / "cover.png"
    manifest = artifact_store.manifest_path_for(target)
    manifest.write_text("{not valid json", encoding="utf-8")
    assert artifact_store.load(target) is None


def test_save_leaves_no_partial_file_on_write_failure(tmp_path, monkeypatch):
    target = tmp_path / "cover.png"
    artifact = _artifact(str(target))

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(artifact_store.json, "dump", _boom)

    import pytest
    with pytest.raises(OSError):
        artifact_store.save(artifact)

    assert not artifact_store.manifest_path_for(target).exists()
    assert list(tmp_path.iterdir()) == []  # no stray temp file left behind


def test_is_cache_valid_true_when_file_and_manifest_match(tmp_path):
    target = tmp_path / "cover.png"
    target.write_bytes(b"fake png")
    artifact = _artifact(str(target))
    artifact_store.save(artifact)

    assert artifact_store.is_cache_valid(target, artifact) is True


def test_is_cache_valid_false_when_artifact_file_missing(tmp_path):
    target = tmp_path / "cover.png"
    target.write_bytes(b"fake png")
    artifact = _artifact(str(target))
    artifact_store.save(artifact)
    target.unlink()

    assert artifact_store.is_cache_valid(target, artifact) is False


def test_is_cache_valid_false_when_inputs_changed(tmp_path):
    target = tmp_path / "cover.png"
    target.write_bytes(b"fake png")
    original = _artifact(str(target), prompt_version="v3")
    artifact_store.save(original)

    changed = _artifact(str(target), prompt_version="v4")
    assert artifact_store.is_cache_valid(target, changed) is False


def test_is_cache_valid_false_when_no_manifest_exists(tmp_path):
    target = tmp_path / "cover.png"
    target.write_bytes(b"fake png")
    assert artifact_store.is_cache_valid(target, _artifact(str(target))) is False
