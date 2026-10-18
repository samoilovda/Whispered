"""The portable project archive (N06): round trip, what stays home, and
hostile input. No real user data: every path is under tmp_path."""

from __future__ import annotations

import hashlib
import json
import zipfile
from types import SimpleNamespace

import pytest

from application import project_archive as pa
from core.history import HistoryStore
from domain import project_manifest as pm
from domain.project_manifest import ProjectError


@pytest.fixture
def world(monkeypatch, tmp_path):
    """A source library and an empty destination one, each with its own
    data and output folders; ``use("src"|"dst")`` switches between them."""
    state = {}

    def make(name):
        root = tmp_path / name
        (root / "data").mkdir(parents=True)
        (root / "output").mkdir()
        state[name] = SimpleNamespace(root=root, store=HistoryStore(db_path=root / "h.db"))

    make("src")
    make("dst")
    current = {"name": "src"}

    def use(name):
        current["name"] = name

    monkeypatch.setattr("core.paths.data_dir", lambda: state[current["name"]].root / "data")
    monkeypatch.setattr("core.paths.output_dir", lambda: state[current["name"]].root / "output")
    monkeypatch.setattr("core.history.get_history_store", lambda: state[current["name"]].store)
    monkeypatch.setattr("application.run_store._db_path", lambda: state[current["name"]].root / "h.db")
    return SimpleNamespace(src=state["src"], dst=state["dst"], use=use, tmp=tmp_path)


def _populate(world):
    from core.paths import artifact_dir

    world.use("src")
    store = world.src.store
    media = world.tmp / "interview.mp4"
    media.write_bytes(b"MEDIA" * 1000)
    payload = {"segments": [{"start": 0.0, "end": 2.0, "text": "hello there", "speaker": None}],
               "language": "en", "duration": 2.0}
    result = SimpleNamespace(segments=[SimpleNamespace(start=0.0, end=2.0, text="hello there",
                             speaker=None)], language="en", duration=2.0)
    rid = store.add(result, source_path=str(media), source_name="interview.mp4", model="m")
    store.set_title(rid, "My interview")
    store.set_artifacts(rid, ["transcript", "article"])
    store.add_transcript_revision(rid, "r1", json.dumps(payload))
    store.add_bookmark(rid, 1.5, "nice")
    store.add_chat_message(rid, "user", "q?", revision="r1")
    store.add_chat_message(rid, "assistant", "a.", revision="r1")
    store.save_cut_plan(rid, {"schema_version": 1, "cuts": [{"start": 0, "end": 1}], "unresolved": []})
    store.save_review_marks(rid, {"schema_version": 1, "marks": [{"start": 0, "end": 2}]})
    from application import run_store
    from application.job_engine import JobRun
    from domain.job import JobSpec, StepSpec

    run_store.save_run(rid, "podcast_article", JobRun(spec=JobSpec(name="r", steps=(StepSpec("clean"),))),
                       status="done")
    folder = artifact_dir(rid, str(media))
    folder.mkdir(parents=True)
    (folder / "articles.json").write_text(json.dumps({"blog_post": {"title": "T", "content": "C"}}))
    (folder / "articles.json.manifest.json").write_text(json.dumps(
        {"record_id": str(rid), "source_hash": "h", "source_path": str(media),
         "transcript_revision": "r", "type": "article", "path": str(folder / "articles.json")}))
    (folder / "youtube_upload.queued.json").write_text('{"video": "x"}')
    (folder / "oauth_token.json").write_text('{"refresh_token": "SECRET"}')
    (folder / "cover.queued.png").write_bytes(b"png")
    (folder / "notes.md").write_text("my notes")
    return rid, media


def _names(path):
    with zipfile.ZipFile(path) as z:
        return set(z.namelist())


def test_round_trip_into_another_library(world):
    rid, media = _populate(world)
    plan = pa.plan_export(rid)
    assert plan.counts["versions"] == 1 and plan.counts["chat"] == 2
    assert sorted(plan.skipped) == ["cover.queued.png", "oauth_token.json", "youtube_upload.queued.json"]
    out = world.tmp / "p.whispered.zip"
    pa.write_export(plan, str(out))
    names = _names(out)
    assert "artifacts/articles.json" in names and "artifacts/notes.md" in names
    assert not any("oauth" in n or "queued" in n for n in names)
    with zipfile.ZipFile(out) as z:
        everything = b"".join(z.read(n) for n in z.namelist())
    assert str(world.tmp).encode() not in everything          # no machine paths anywhere
    assert b"SECRET" not in everything

    world.use("dst")
    preview = pa.inspect_archive(str(out))
    assert preview.title == "My interview" and "no_media" in preview.warnings
    new_id = pa.import_archive(str(out))
    dst = world.dst.store
    assert new_id == 1 and world.dst.store.count() == 1
    full = dst.get_record(new_id)
    assert full["title"] == "My interview" and full["payload"]["segments"][0]["text"] == "hello there"
    assert full["source_path"].endswith("interview.mp4") and not __import__("os").path.exists(full["source_path"])
    assert [m["content"] for m in dst.list_chat_messages(new_id)] == ["q?", "a."]
    assert dst.get_cut_plan(new_id)["cuts"] and dst.get_review_marks(new_id)["marks"]
    assert len(dst.list_bookmarks(new_id)) == 1 and len(dst.list_transcript_revisions(new_id)) == 1
    from application import run_store
    assert run_store.load_latest_run(new_id).recipe == "podcast_article"
    from core.paths import artifact_dir
    folder = artifact_dir(new_id, full["source_path"])
    assert (folder / "articles.json").exists() and (folder / "notes.md").read_text() == "my notes"
    assert not (folder / "oauth_token.json").exists()
    assert json.loads((folder / "articles.json.manifest.json").read_text())["source_path"] == ""
    assert dst.search_artifacts("blog") or dst.search_artifacts("C")   # materials are searchable
    assert not (world.dst.root / "data" / "import-staging").exists()


def test_import_never_touches_existing_records_and_marks_a_same_titled_one(world):
    rid, _ = _populate(world)
    out = world.tmp / "p.whispered.zip"
    pa.write_export(pa.plan_export(rid), str(out))
    world.use("dst")
    existing = world.dst.store.add(
        SimpleNamespace(segments=[], language="en", duration=1.0), source_path="/x.mp3")
    world.dst.store.set_title(existing, "My interview")
    first = pa.import_archive(str(out))
    second = pa.import_archive(str(out))
    assert len({existing, first, second}) == 3
    assert world.dst.store.get_record(existing)["title"] == "My interview"
    assert world.dst.store.get_record(first)["title"] == "My interview (imported)"


def test_media_is_included_only_when_asked_and_survives_the_trip(world):
    rid, media = _populate(world)
    without = pa.plan_export(rid)
    assert not without.include_media
    plan = pa.plan_export(rid, include_media=True)
    assert plan.include_media and plan.media_name == "interview.mp4"
    out = world.tmp / "m.whispered.zip"
    manifest = pa.write_export(plan, str(out))
    assert manifest.media_included
    world.use("dst")
    new_id = pa.import_archive(str(out))
    path = world.dst.store.get_record(new_id)["source_path"]
    assert open(path, "rb").read() == media.read_bytes()


def test_missing_media_is_a_warning_not_a_failure(world):
    rid, media = _populate(world)
    media.unlink()
    plan = pa.plan_export(rid, include_media=True)
    assert not plan.include_media and plan.warnings == ["media_missing"]


def test_cancelling_an_export_leaves_nothing(world):
    rid, _ = _populate(world)
    out = world.tmp / "c.whispered.zip"
    with pytest.raises(pa.ProjectCancelled):
        pa.write_export(pa.plan_export(rid), str(out), cancelled=lambda: True)
    assert not out.exists() and not list(world.tmp.glob(".project.*"))


def test_a_failed_import_removes_everything_it_created(world, monkeypatch):
    rid, _ = _populate(world)
    out = world.tmp / "p.whispered.zip"
    pa.write_export(pa.plan_export(rid), str(out))
    world.use("dst")

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(pa.shutil, "move", boom)
    with pytest.raises(OSError):
        pa.import_archive(str(out))
    assert world.dst.store.count() == 0
    assert not (world.dst.root / "data" / "import-staging").exists()
    assert not list((world.dst.root / "output").iterdir())


# ----------------------------------------------------------------- hostile

def _craft(path, members, *, overrides=None, listed=None, compression=zipfile.ZIP_STORED):
    files = []
    for name, data in members.items():
        if listed is not None and name not in listed:
            continue
        files.append({"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                      "kind": "record" if name.startswith("record/") else "artifact"})
    manifest = {"format": pm.FORMAT, "format_version": 1, "created_at": "", "app_version": "t",
                "record": {"title": "T"}, "counts": {}, "media": {"included": False},
                "files": files}
    manifest.update(overrides or {})
    with zipfile.ZipFile(path, "w", compression=compression) as z:
        for name, data in members.items():
            z.writestr(name, data)
        z.writestr(pm.MANIFEST_NAME, json.dumps(manifest))
    return path


_GOOD = {"record/transcript.json": json.dumps(
    {"json_payload": json.dumps({"segments": []}), "source_name": "a.mp3"}).encode()}


@pytest.mark.parametrize("name", [
    "../evil.txt", "/abs/evil.txt", "artifacts/../../evil", "artifacts\\evil.txt",
    "C:/evil.txt", "artifacts/con.txt", "artifacts/dot.", "artifacts//double",
])
def test_unsafe_member_names_are_refused_before_anything_is_written(world, name):
    path = _craft(world.tmp / "h.zip", {**_GOOD, name: b"x"})
    with pytest.raises(ProjectError):
        pa.inspect_archive(str(path))
    world.use("dst")
    with pytest.raises(ProjectError):
        pa.import_archive(str(path))
    assert world.dst.store.count() == 0 and not (world.tmp / "evil.txt").exists()


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_symlinks_and_duplicates_and_unlisted_or_missing_files_are_refused(world):
    path = world.tmp / "link.zip"
    _craft(path, _GOOD)
    with zipfile.ZipFile(path, "a") as z:
        info = zipfile.ZipInfo("artifacts/link")
        info.external_attr = (0o120777 << 16)
        z.writestr(info, "/etc/passwd")
    with pytest.raises(ProjectError, match="links"):
        pa.inspect_archive(str(path))
    dup = world.tmp / "dup.zip"
    _craft(dup, _GOOD)
    with zipfile.ZipFile(dup, "a") as z:
        z.writestr("record/transcript.json", b"{}")
    with pytest.raises(ProjectError, match="twice"):
        pa.inspect_archive(str(dup))
    extra = _craft(world.tmp / "extra.zip", {**_GOOD, "artifacts/a.txt": b"x"},
                   listed={"record/transcript.json"})
    with pytest.raises(ProjectError, match="not listed"):
        pa.inspect_archive(str(extra))
    missing = world.tmp / "missing.zip"
    _craft(missing, _GOOD, overrides={"files": [
        *json.loads(json.dumps([{"path": "record/transcript.json", "size": len(_GOOD["record/transcript.json"]),
                                 "sha256": hashlib.sha256(_GOOD["record/transcript.json"]).hexdigest(),
                                 "kind": "record"}]),),
        {"path": "artifacts/gone.txt", "size": 1, "sha256": "0" * 64, "kind": "artifact"}]})
    with pytest.raises(ProjectError, match="missing"):
        pa.inspect_archive(str(missing))


def test_wrong_checksum_newer_version_and_not_a_project_are_refused(world):
    bad = _craft(world.tmp / "sum.zip", {**_GOOD, "artifacts/a.txt": b"real"})
    # Rewrite a member after the manifest was computed: contents no longer match.
    with zipfile.ZipFile(bad) as z:
        members = {n: z.read(n) for n in z.namelist()}
    members["artifacts/a.txt"] = b"fake"
    with zipfile.ZipFile(bad, "w") as z:
        for n, d in members.items():
            z.writestr(n, d)
    with pytest.raises(ProjectError, match="size|checksum"):
        world.use("dst")
        pa.import_archive(str(bad))
    assert world.dst.store.count() == 0

    same_size = _craft(world.tmp / "same.zip", {**_GOOD, "artifacts/a.txt": b"real"})
    with zipfile.ZipFile(same_size) as z:
        members = {n: z.read(n) for n in z.namelist()}
    members["artifacts/a.txt"] = b"fake"           # same length, different content
    with zipfile.ZipFile(same_size, "w") as z:
        for n, d in members.items():
            z.writestr(n, d)
    with pytest.raises(ProjectError, match="checksum"):
        pa.import_archive(str(same_size))
    assert world.dst.store.count() == 0 and not (world.dst.root / "data" / "import-staging").exists()

    newer = _craft(world.tmp / "newer.zip", _GOOD, overrides={"format_version": 99})
    with pytest.raises(ProjectError, match="newer"):
        pa.inspect_archive(str(newer))
    text = world.tmp / "x.zip"
    text.write_text("not a zip")
    with pytest.raises(ProjectError, match="zip"):
        pa.inspect_archive(str(text))
    other = world.tmp / "o.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(ProjectError, match="not a Whispered"):
        pa.inspect_archive(str(other))


def test_a_zip_bomb_is_refused_by_ratio(world):
    path = world.tmp / "bomb.zip"
    big = b"\0" * (pm.RATIO_FLOOR + 1024 * 1024)
    _craft(path, {**_GOOD, "artifacts/zeros.bin": big}, compression=zipfile.ZIP_DEFLATED)
    with pytest.raises(ProjectError, match="ratio"):
        pa.inspect_archive(str(path))


def test_a_member_that_lies_about_its_size_is_stopped_while_unpacking(world, monkeypatch):
    path = _craft(world.tmp / "lie.zip", {**_GOOD, "artifacts/a.txt": b"abcd"})
    monkeypatch.setattr(pa, "_check_zip", lambda archive: {i.filename: i for i in archive.infolist()})
    # The manifest says 2 bytes for a member that really holds 4.
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read(pm.MANIFEST_NAME))
        members = {n: z.read(n) for n in z.namelist() if n != pm.MANIFEST_NAME}
    for entry in manifest["files"]:
        if entry["path"] == "artifacts/a.txt":
            entry["size"] = 2
    with zipfile.ZipFile(path, "w") as z:
        for n, d in members.items():
            z.writestr(n, d)
        z.writestr(pm.MANIFEST_NAME, json.dumps(manifest))
    monkeypatch.setattr(pa, "inspect_archive", lambda p: pa.ImportPlan(
        p, pm.parse_manifest(manifest), 100))
    world.use("dst")
    with pytest.raises(ProjectError, match="larger than it claims"):
        pa.import_archive(str(path))
    assert world.dst.store.count() == 0


def test_manifest_rules():
    assert pm.unsafe_member("artifacts/ok/file.txt") is None
    assert pm.excluded_artifact("sub/youtube_upload.queued.json")
    assert pm.excluded_artifact("OAuth_client.json")
    assert not pm.excluded_artifact("articles.json")
