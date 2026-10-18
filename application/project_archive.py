"""Exporting a record as a portable project and importing one (N06).

Qt-free. ``plan_export`` / ``inspect_archive`` are the dry runs: they say
what an archive would hold or what importing would add, touching nothing.
Export writes one ``*.whispered.zip`` (a manifest with SHA-256 of every
file, the record's data as JSON, its generated files, and — only if asked —
the media). Import validates the archive as hostile input (names, links,
sizes, ratios, checksums), unpacks it into a staging folder inside the app
data, then adds a **new** record with a new local id in a single database
transaction and moves its files into place; any failure removes what the
import itself created and leaves everything else as it was.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from core.logger import get_logger
from domain import project_manifest as pm
from domain.project_manifest import FileEntry, ProjectError, ProjectManifest

logger = get_logger(__name__)

_CHUNK = 1 << 20
Cancelled = Callable[[], bool]
Progress = Callable[[int, int], None]


class ProjectCancelled(RuntimeError):
    """The user cancelled; nothing was left behind."""


def _store():
    from core.history import get_history_store

    return get_history_store()


def _sha256(path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _clean_name(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/")) or "media"
    cleaned = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in base).strip(". ")
    return cleaned[:120] or "media"


# ============================================================== export

@dataclass
class PlannedFile:
    path: str                   # name inside the archive
    kind: str                   # record | artifact | media
    source: Optional[Path] = None
    data: Optional[bytes] = None
    size: int = 0


@dataclass
class ExportPlan:
    record_id: int
    title: str
    files: list[PlannedFile]
    counts: dict[str, int]
    media_name: str = ""
    media_fingerprint: str = ""
    include_media: bool = False
    media_size: int = 0          # the media file's size when it is on disk (even if not included)
    skipped: list[str] = field(default_factory=list)    # generated files kept out (credentials…)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _sanitized_artifact(source: Path) -> Optional[bytes]:
    """A provenance manifest minus the machine's absolute source path."""
    if not source.name.endswith(".manifest.json"):
        return None
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # Machine-specific: where the media was, and where this file lived.
    data["source_path"] = ""
    data["path"] = source.name[: -len(".manifest.json")]
    return _json_bytes(data)


def plan_export(record_id: int, *, include_media: bool = False) -> ExportPlan:
    """What exporting *record_id* would write (the dry run)."""
    from core.paths import artifact_dir

    store = _store()
    rows = store.export_record_rows(record_id)
    full = store.get_record(record_id)
    if rows is None or full is None:
        raise ProjectError("the record no longer exists")
    record = rows["record"]
    files: list[PlannedFile] = []

    def add_json(name: str, value: Any) -> None:
        data = _json_bytes(value)
        files.append(PlannedFile(f"{pm.RECORD_DIR}{name}.json", "record", data=data, size=len(data)))

    add_json("transcript", record)
    add_json("versions", rows["versions"])
    add_json("bookmarks", rows["bookmarks"])
    add_json("chat", rows["chat"])
    add_json("runs", rows["runs"])
    if rows["cut_plan"] is not None:
        add_json("cut_plan", rows["cut_plan"])
    if rows["review_marks"] is not None:
        add_json("review_marks", rows["review_marks"])

    skipped: list[str] = []
    folder = artifact_dir(record_id, full["source_path"] or "recording")
    if folder.is_dir():
        for source in sorted(folder.rglob("*")):
            if source.is_symlink() or not source.is_file():
                continue
            relative = source.relative_to(folder).as_posix()
            if pm.excluded_artifact(relative):
                skipped.append(relative)
                continue
            replaced = _sanitized_artifact(source)
            files.append(PlannedFile(
                f"{pm.ARTIFACT_DIR}{relative}", "artifact",
                source=None if replaced is not None else source, data=replaced,
                size=len(replaced) if replaced is not None else source.stat().st_size))

    plan = ExportPlan(
        record_id, full["title"] or full["source_name"] or f"record-{record_id}", files,
        counts={"versions": len(rows["versions"]), "bookmarks": len(rows["bookmarks"]),
                "chat": len(rows["chat"]), "runs": len(rows["runs"]),
                "artifacts": sum(1 for f in files if f.kind == "artifact")},
        skipped=skipped,
    )
    media = Path(full.get("media_path") or full["source_path"] or "")
    if media.is_file():
        plan.media_size = media.stat().st_size
    if include_media:
        if media.is_file():
            plan.include_media = True
            plan.media_name = _clean_name(media.name)
            files.append(PlannedFile(f"{pm.MEDIA_DIR}{plan.media_name}", "media", source=media,
                                     size=plan.media_size))
        else:
            plan.warnings.append("media_missing")
    return plan


def write_export(
    plan: ExportPlan, destination: str, *, cancelled: Cancelled = lambda: False,
    progress: Optional[Progress] = None,
) -> ProjectManifest:
    """Write *plan* to *destination* (atomically: a ``.part`` file replaced
    into place only when complete)."""
    from version import __version__

    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    entries: list[FileEntry] = []
    done, total = 0, max(1, plan.total_bytes)
    fd, tmp_name = tempfile.mkstemp(prefix=".project.", suffix=".part", dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", allowZip64=True) as archive:
            for item in plan.files:
                if cancelled():
                    raise ProjectCancelled("cancelled")
                method = zipfile.ZIP_STORED if item.kind == "media" else zipfile.ZIP_DEFLATED
                digest, size = hashlib.sha256(), 0
                info = zipfile.ZipInfo(item.path, date_time=datetime.now().timetuple()[:6])
                info.compress_type = method
                info.external_attr = 0o644 << 16
                with archive.open(info, "w", force_zip64=True) as out:
                    if item.data is not None:
                        out.write(item.data)
                        digest.update(item.data)
                        size = len(item.data)
                    else:
                        assert item.source is not None
                        with open(item.source, "rb") as handle:
                            while chunk := handle.read(_CHUNK):
                                if cancelled():
                                    raise ProjectCancelled("cancelled")
                                out.write(chunk)
                                digest.update(chunk)
                                size += len(chunk)
                                done += len(chunk)
                                if progress:
                                    progress(done, total)
                if item.data is not None:
                    done += size
                    if progress:
                        progress(done, total)
                entries.append(FileEntry(item.path, size, digest.hexdigest(), item.kind))
            transcript = json.loads(next(f.data for f in plan.files
                                         if f.path == f"{pm.RECORD_DIR}transcript.json" and f.data))
            manifest = ProjectManifest(
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                app_version=__version__,
                record={k: transcript.get(k) for k in
                        ("title", "source_name", "source_kind", "duration", "language", "model",
                         "created_at")},
                files=entries, media_included=plan.include_media, media_name=plan.media_name,
                media_fingerprint=transcript.get("media_fingerprint") or "",
                counts=plan.counts,
            )
            archive.writestr(pm.MANIFEST_NAME, _json_bytes(manifest.to_dict()))
        os.replace(tmp, dest)
        return manifest
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# ============================================================== import

@dataclass
class ImportPlan:
    path: str
    manifest: ProjectManifest
    total_bytes: int
    warnings: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return str(self.manifest.record.get("title") or self.manifest.record.get("source_name") or "")


def _check_zip(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Refuse a hostile archive before anything is read from it."""
    infos = archive.infolist()
    if len(infos) > pm.MAX_MEMBERS + 1:
        raise ProjectError("too many files in the archive")
    by_name: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in infos:
        if info.is_dir():
            continue
        problem = pm.unsafe_member(info.filename)
        if problem:
            raise ProjectError(f"unsafe file name {info.filename!r}: {problem}")
        if info.filename in by_name:
            raise ProjectError(f"{info.filename}: appears twice")
        if info.flag_bits & 0x1:
            raise ProjectError("encrypted archives are not supported")
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode and (stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode)
                     or stat.S_ISSOCK(mode)):
            raise ProjectError(f"{info.filename}: links and special files are not allowed")
        if info.file_size > pm.MAX_MEMBER_BYTES:
            raise ProjectError(f"{info.filename}: too large")
        if (info.file_size > pm.RATIO_FLOOR
                and info.file_size > max(1, info.compress_size) * pm.MAX_RATIO):
            raise ProjectError(f"{info.filename}: suspicious compression ratio")
        total += info.file_size
        by_name[info.filename] = info
    if total > pm.MAX_TOTAL_BYTES:
        raise ProjectError("the archive would unpack to more than the allowed size")
    return by_name


def inspect_archive(path: str) -> ImportPlan:
    """Dry run: validate *path* and say what importing would add. Reads
    only the archive's directory and manifest."""
    try:
        with zipfile.ZipFile(path) as archive:
            by_name = _check_zip(archive)
            info = by_name.get(pm.MANIFEST_NAME)
            if info is None:
                raise ProjectError("not a Whispered project file")
            if info.file_size > pm.MAX_JSON_BYTES:
                raise ProjectError("the manifest is too large")
            manifest = pm.parse_manifest(json.loads(archive.read(info)))
    except zipfile.BadZipFile as exc:
        raise ProjectError("not a readable zip file") from exc
    except ValueError as exc:
        if isinstance(exc, ProjectError):
            raise
        raise ProjectError("the manifest is damaged") from exc
    listed = {entry.path for entry in manifest.files}
    actual = set(by_name) - {pm.MANIFEST_NAME}
    if actual - listed:
        raise ProjectError(f"{sorted(actual - listed)[0]}: not listed in the manifest")
    if listed - actual:
        raise ProjectError(f"{sorted(listed - actual)[0]}: listed but missing")
    for entry in manifest.files:
        if by_name[entry.path].file_size != entry.size:
            raise ProjectError(f"{entry.path}: size differs from the manifest")
    plan = ImportPlan(path, manifest, sum(e.size for e in manifest.files))
    if not manifest.media_included:
        plan.warnings.append("no_media")
    return plan


def _extract(path: str, plan: ImportPlan, staging: Path, cancelled: Cancelled,
             progress: Optional[Progress]) -> None:
    """Unpack into *staging*, counting real bytes (a header can lie) and
    checking every file's SHA-256."""
    done, total = 0, max(1, plan.total_bytes)
    with zipfile.ZipFile(path) as archive:
        for entry in plan.manifest.files:
            if cancelled():
                raise ProjectCancelled("cancelled")
            target = staging / entry.path
            if staging.resolve() not in target.resolve().parents:
                raise ProjectError(f"{entry.path}: escapes the import folder")
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with archive.open(entry.path) as src, open(target, "wb") as out:
                while chunk := src.read(_CHUNK):
                    size += len(chunk)
                    if size > entry.size:
                        raise ProjectError(f"{entry.path}: larger than it claims")
                    digest.update(chunk)
                    out.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
                    if cancelled():
                        raise ProjectCancelled("cancelled")
            if size != entry.size or digest.hexdigest() != entry.sha256:
                raise ProjectError(f"{entry.path}: damaged (checksum differs)")


def _rebind_manifests(folder: Path, record_id: int) -> None:
    """Point the imported provenance manifests at the new record and the
    new location of their files (they were exported without either)."""
    for manifest in folder.rglob("*.manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            data["record_id"] = str(record_id)
            data["path"] = str(manifest.with_name(manifest.name[: -len(".manifest.json")]))
            manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, ValueError) as exc:
            logger.warning("Could not rebind %s: %s", manifest, exc)


def _read_json(staging: Path, name: str, default: Any = None) -> Any:
    path = staging / f"{pm.RECORD_DIR}{name}.json"
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ProjectError(f"{name}: damaged") from exc


def import_archive(
    path: str, *, cancelled: Cancelled = lambda: False, progress: Optional[Progress] = None,
) -> int:
    """Import *path* as a new record; returns its new id. Everything the
    import created is removed again if anything fails."""
    from core.paths import artifact_dir, data_dir

    plan = inspect_archive(path)
    root = data_dir()
    staging = root / "import-staging" / uuid.uuid4().hex[:12]
    staging.mkdir(parents=True)
    new_id: Optional[int] = None
    made: list[Path] = []
    store = _store()
    try:
        _extract(path, plan, staging, cancelled, progress)
        record = _read_json(staging, "transcript")
        if not isinstance(record, dict) or not isinstance(record.get("json_payload"), str):
            raise ProjectError("the transcript is damaged")
        try:
            payload = json.loads(record["json_payload"])
        except ValueError as exc:
            raise ProjectError("the transcript is damaged") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
            raise ProjectError("the transcript is damaged")

        # Where the media would live: it names the output folder, and (if
        # included) is where the file goes. A missing one offers "Find file…".
        name = _clean_name(plan.manifest.media_name or str(record.get("source_name") or "media"))
        media_dir = root / "imported_media" / uuid.uuid4().hex[:10]
        source_path = str(media_dir / name)
        if plan.manifest.media_included:
            media_dir.mkdir(parents=True)
            made.append(media_dir)
            shutil.move(str(staging / f"{pm.MEDIA_DIR}{plan.manifest.media_name}"), source_path)

        record = dict(record)
        title = str(record.get("title") or "")
        if title and any(r.title == title for r in store.list(limit=100_000)):
            record["title"] = f"{title} (imported)"       # never confused with the original
        data = {
            "record": record, "versions": _read_json(staging, "versions", []),
            "bookmarks": _read_json(staging, "bookmarks", []),
            "cut_plan": _read_json(staging, "cut_plan"), "review_marks": _read_json(staging, "review_marks"),
            "chat": _read_json(staging, "chat", []), "runs": _read_json(staging, "runs", []),
        }
        new_id = store.import_record_rows(data, source_path)

        source_dir = staging / "artifacts"
        if source_dir.is_dir():
            target = artifact_dir(new_id, source_path)
            if target.exists() and any(target.iterdir()):
                raise ProjectError("the record's output folder already exists")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_dir), str(target))
            made.append(target)
            _rebind_manifests(target, new_id)
        try:
            from application.steps import reindex_record

            reindex_record(new_id, source_path, json.loads(record.get("artifacts") or "[]"))
        except Exception as exc:  # noqa: BLE001 - search over materials is best-effort
            logger.warning("Imported record %s: materials not indexed: %s", new_id, exc)
        return new_id
    except BaseException:
        if new_id is not None:
            try:
                store.delete(new_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not remove the half-imported record %s: %s", new_id, exc)
        for folder in made:
            shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        try:
            (root / "import-staging").rmdir()
        except OSError:
            pass
