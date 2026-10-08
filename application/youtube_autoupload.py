"""Queue of approved YouTube uploads, run without the app.

The publish wizard's last step can "queue for upload": it writes exactly
what was approved — video, title, description with timecodes, tags,
visibility, cover — to ``<artifact_dir>/youtube_upload.queued.json``.
``tools/youtube_autoupload.py`` then uploads every queued record that has
not been uploaded yet, through the same ``core.youtube_upload.UploadClient``
and the same per-record files the wizard uses (``youtube_upload.json``
once done, ``youtube_upload.pending.json`` to resume an interrupted
upload), so the wizard and the script recognise each other's uploads.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Protocol

from application.youtube_publish import validate_package
from domain.youtube_publish import PublishPackage, UploadRecord

QUEUE_FILE = "youtube_upload.queued.json"
RECORD_FILE = "youtube_upload.json"
PENDING_FILE = "youtube_upload.pending.json"
# YouTube Data API: 10,000 quota units a day, videos.insert costs 1,600
# and thumbnails.set 50 — six uploads with covers fit in one day.
UPLOADS_PER_DAY = 6


@dataclass(frozen=True)
class QueuedUpload:
    """One approved package waiting in (or done with) the queue."""

    artifact_dir: Path
    record_id: Optional[int]
    package: PublishPackage
    queued_at: str

    @property
    def record_path(self) -> Path:
        return self.artifact_dir / RECORD_FILE

    @property
    def pending_path(self) -> Path:
        return self.artifact_dir / PENDING_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def queue_upload(
    artifact_dir: Path, record_id: Optional[int], pkg: PublishPackage,
) -> Path:
    """Write *pkg* as the record's approved upload (replacing an earlier
    one) and return the queue file's path."""
    path = artifact_dir / QUEUE_FILE
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if pkg.thumbnail_path is not None and pkg.thumbnail_path.is_file():
        # Freeze the approved cover: cover.png is redrawn whenever the
        # cover is approved again, and the queue must upload what was
        # agreed, not whatever is there by the time the script runs.
        frozen = artifact_dir / f"cover.queued{pkg.thumbnail_path.suffix.lower()}"
        if pkg.thumbnail_path.resolve() != frozen.resolve():
            shutil.copyfile(pkg.thumbnail_path, frozen)
        pkg = replace(pkg, thumbnail_path=frozen)
    data = {
        "record_id": record_id,
        "queued_at": _now(),
        "video_path": str(pkg.video_path),
        "title": pkg.title,
        "description": pkg.description,
        "tags": list(pkg.tags),
        "thumbnail_path": str(pkg.thumbnail_path) if pkg.thumbnail_path else None,
        "privacy": pkg.privacy,
        "language": pkg.language,
    }
    fd, tmp = tempfile.mkstemp(dir=artifact_dir, prefix=".queue-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def load_queued(path: Path) -> Optional[QueuedUpload]:
    """The queued upload at *path*, or ``None`` when it is missing/corrupt."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        thumbnail = data.get("thumbnail_path")
        pkg = PublishPackage(
            video_path=Path(data["video_path"]),
            title=str(data["title"]),
            description=str(data.get("description") or ""),
            tags=tuple(str(tag) for tag in data.get("tags") or ()),
            thumbnail_path=Path(thumbnail) if thumbnail else None,
            privacy=str(data.get("privacy") or "private"),
            language=data.get("language"),
        )
        record_id = data.get("record_id")
        return QueuedUpload(
            artifact_dir=path.parent,
            record_id=int(record_id) if record_id is not None else None,
            package=pkg,
            queued_at=str(data.get("queued_at") or ""),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def find_queued(root: Path) -> list[QueuedUpload]:
    """Every queued upload under *root* (the app's output folder), oldest
    first."""
    items = [load_queued(path) for path in sorted(root.glob(f"*/{QUEUE_FILE}"))]
    return sorted((item for item in items if item is not None), key=lambda item: item.queued_at)


def uploaded_record(item: QueuedUpload) -> Optional[UploadRecord]:
    from core.youtube_upload import load_upload_record

    return load_upload_record(item.record_path)


def problems(item: QueuedUpload) -> list[str]:
    """Why *item* cannot be uploaded as queued (empty when it can)."""
    found = []
    if not item.package.video_path.is_file():
        found.append(f"video not found: {item.package.video_path}")
    for issue in validate_package(item.package):
        if issue.blocking and issue.kind != "video_missing":
            found.append(issue.kind)
    return found


class Uploader(Protocol):
    def publish(self, pkg: PublishPackage, *, pending_path: Path, record_path: Path,
                on_progress: Callable[[int, int], None] = ...,
                is_cancelled: Callable[[], bool] = ...) -> object: ...


@dataclass(frozen=True)
class Outcome:
    item: QueuedUpload
    status: str                    # "uploaded" | "skipped" | "failed" | "planned"
    detail: str = ""
    video_id: str = ""


def run_queue(
    items: list[QueuedUpload],
    uploader: Optional[Uploader],
    *,
    limit: int = UPLOADS_PER_DAY,
    force: bool = False,
    on_progress: Callable[[QueuedUpload, int, int], None] = lambda item, sent, total: None,
    on_uploaded: Callable[[QueuedUpload, UploadRecord], None] = lambda item, record: None,
) -> list[Outcome]:
    """Upload *items* in order, at most *limit* of them. ``uploader=None``
    is a dry run: it reports what would be uploaded. An item already
    uploaded is skipped unless *force*; one failure does not stop the rest
    (its resumable session is kept for the next run)."""
    outcomes: list[Outcome] = []
    started = 0
    for item in items:
        done = uploaded_record(item)
        if done is not None and not force:
            outcomes.append(Outcome(item, "skipped", "already uploaded", done.video_id))
            continue
        issues = problems(item)
        if issues:
            outcomes.append(Outcome(item, "failed", "; ".join(issues)))
            continue
        if started >= limit:
            outcomes.append(Outcome(item, "skipped", f"daily limit of {limit} uploads reached"))
            continue
        started += 1
        if uploader is None:
            outcomes.append(Outcome(item, "planned"))
            continue
        try:
            result = uploader.publish(
                item.package, pending_path=item.pending_path, record_path=item.record_path,
                on_progress=functools.partial(on_progress, item),
            )
        except Exception as exc:  # report, keep going with the next record
            outcomes.append(Outcome(item, "failed", f"{type(exc).__name__}: {exc}"))
            continue
        record = getattr(result, "record")
        warning = getattr(result, "thumbnail_warning", None) or ""
        on_uploaded(item, record)
        outcomes.append(Outcome(item, "uploaded", warning, record.video_id))
    return outcomes
