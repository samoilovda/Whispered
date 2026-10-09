"""application.artifacts.record_export — shared provenance write."""

from application import artifacts


def test_record_export_builds_and_saves(monkeypatch):
    saved = []
    monkeypatch.setattr(artifacts, "save_best_effort", lambda a: saved.append(a) or True)
    ok = artifacts.record_export(
        record_id=None,
        source_path=None,
        segments=[{"text": "hi"}],
        language=None,
        type="youtube_tags",
        path="/tmp/x.txt",
    )
    assert ok
    art = saved[0]
    assert art.record_id == "unsaved"
    assert art.type == "youtube_tags"
    assert art.path == "/tmp/x.txt"


def test_save_best_effort_swallows_errors(monkeypatch):
    from infrastructure.persistence import artifact_store

    def boom(_artifact):
        raise OSError("disk full")

    monkeypatch.setattr(artifact_store, "save", boom)
    art = artifacts.Artifact("1", "h", "s", "r", "cover", "p")
    assert artifacts.save_best_effort(art) is False
