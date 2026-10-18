"""The portable project archive's manifest and its safety rules (N06).

A project is one record — transcript, versions, bookmarks, cut, review
marks, chat, runs, generated files and, if the user chose, the media —
packed in a ``*.whispered.zip``. The manifest lists every file with its
size and SHA-256, never an absolute path of the machine it came from.
Credentials (API keys, OAuth tokens, the queue of approved uploads) are
never included: publishing is not something a project file can authorize.

Pure rules, no I/O: what a member name may look like, which generated
files stay home, what a readable manifest is, and the size limits that
guard against hostile archives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

FORMAT = "whispered-project"
FORMAT_VERSION = 1

MANIFEST_NAME = "manifest.json"
RECORD_DIR = "record/"
ARTIFACT_DIR = "artifacts/"
MEDIA_DIR = "media/"

# Hostile-archive limits.
MAX_MEMBERS = 5_000
MAX_TOTAL_BYTES = 8 * 1024 ** 3        # everything unpacked, media included
MAX_MEMBER_BYTES = 6 * 1024 ** 3
MAX_JSON_BYTES = 200 * 1024 ** 2       # a section / the manifest
MAX_RATIO = 300                        # unpacked / packed, for members over 10 MB
RATIO_FLOOR = 10 * 1024 ** 2

SECTIONS = ("transcript", "versions", "bookmarks", "cut_plan", "review_marks", "chat", "runs")

# Generated files that must never leave the machine with a project.
_EXCLUDED_NAMES = re.compile(
    r"(?i)^(youtube_upload[^/]*|cover\.queued\.png|.*oauth.*|.*token.*|.*credential.*|.*secret.*|"
    r".*\.pem|.*\.key)$"
)
# Windows device names: a file called "con.txt" can't be created there.
_WINDOWS_RESERVED = re.compile(r"(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$")


class ProjectError(ValueError):
    """The archive cannot be used (message says why)."""


def excluded_artifact(relative_name: str) -> bool:
    """Whether a generated file stays out of the archive (credentials, the
    approved-upload queue)."""
    return bool(_EXCLUDED_NAMES.match(relative_name.rsplit("/", 1)[-1]))


def unsafe_member(name: str) -> Optional[str]:
    """Why an archive member name cannot be extracted, or ``None`` if it
    can: absolute paths, ``..``, backslashes, drive letters, empty or
    control-character parts, Windows device names, trailing dots/spaces."""
    if not name or name.startswith("/") or "\\" in name or re.match(r"^[A-Za-z]:", name):
        return "absolute or non-portable path"
    if any(ord(ch) < 32 for ch in name):
        return "control characters"
    parts = name.rstrip("/").split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return "path traversal"
        if _WINDOWS_RESERVED.match(part):
            return "reserved Windows name"
        if part != part.rstrip(". "):
            return "trailing dot or space"
        if len(part) > 200:
            return "name too long"
    return None


@dataclass(frozen=True)
class FileEntry:
    path: str
    size: int
    sha256: str
    kind: str          # "record" | "artifact" | "media"


@dataclass
class ProjectManifest:
    created_at: str
    app_version: str
    record: dict[str, Any]
    files: list[FileEntry]
    media_included: bool = False
    media_name: str = ""
    media_fingerprint: str = ""
    counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "format": FORMAT, "format_version": FORMAT_VERSION,
            "created_at": self.created_at, "app_version": self.app_version,
            "record": self.record, "counts": self.counts,
            "media": {"included": self.media_included, "name": self.media_name,
                      "fingerprint": self.media_fingerprint},
            "files": [{"path": f.path, "size": f.size, "sha256": f.sha256, "kind": f.kind}
                      for f in self.files],
        }


def parse_manifest(data: Any) -> ProjectManifest:
    """Validate a manifest read from an archive; raises :class:`ProjectError`."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ProjectError("not a Whispered project file")
    version = data.get("format_version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise ProjectError("made by a newer Whispered (unsupported project version)")
    if version < 1:
        raise ProjectError("unsupported project version")
    entries = []
    seen = set()
    for raw in data.get("files", []):
        try:
            entry = FileEntry(str(raw["path"]), int(raw["size"]), str(raw["sha256"]),
                              str(raw.get("kind", "artifact")))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectError("the file list is damaged") from exc
        problem = unsafe_member(entry.path)
        if problem:
            raise ProjectError(f"unsafe path {entry.path!r}: {problem}")
        if entry.path in seen:
            raise ProjectError(f"{entry.path}: listed twice")
        if entry.size < 0 or not re.fullmatch(r"[0-9a-f]{64}", entry.sha256):
            raise ProjectError(f"{entry.path}: bad size or checksum")
        if entry.kind not in ("record", "artifact", "media"):
            raise ProjectError(f"{entry.path}: unknown kind")
        seen.add(entry.path)
        entries.append(entry)
    if len(entries) > MAX_MEMBERS:
        raise ProjectError("too many files")
    if sum(e.size for e in entries) > MAX_TOTAL_BYTES:
        raise ProjectError("the project is larger than the allowed size")
    for section in ("transcript",):
        if f"{RECORD_DIR}{section}.json" not in seen:
            raise ProjectError(f"the {section} is missing")
    raw_media = data.get("media")
    media: dict = raw_media if isinstance(raw_media, dict) else {}
    raw_record = data.get("record")
    record: dict = raw_record if isinstance(raw_record, dict) else {}
    return ProjectManifest(
        created_at=str(data.get("created_at", "")), app_version=str(data.get("app_version", "")),
        record=record, files=entries, media_included=bool(media.get("included")),
        media_name=str(media.get("name", "")), media_fingerprint=str(media.get("fingerprint", "")),
        counts={str(k): int(v) for k, v in (data.get("counts") or {}).items()
                if isinstance(v, int)},
    )
