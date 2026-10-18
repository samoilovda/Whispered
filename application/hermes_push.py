"""Push finished records to the user's own server (Hermes) over SSH.

A record's artifact folder that holds ``transcript.speakers.json`` (written
by the ``diarize`` step) is copied to ``<target>/<folder name>/`` with
``rsync`` (``scp`` when rsync is not installed). The files go first and
``ready.json`` last, so a watcher on the server can treat the arrival of
``ready.json`` as "the folder is complete" — no polling of this machine
and no LLM involved.

State lives in the folder itself (``hermes_push.json``: a digest of what
was sent), so the push is idempotent: nothing is re-sent until the
transcript or the YouTube package changes. Qt-free; the network is touched
only when ``Config.hermes_push_target`` is set (CLAUDE.md, rule 1).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

SPEAKERS_JSON = "transcript.speakers.json"
# Sent when present: the readable transcript, the generated YouTube texts
# and the upload record (video id) once the video is on YouTube.
OPTIONAL_FILES = (
    "transcript.speakers.md", "youtube_package.json", "youtube_upload.json",
)
MARKER_FILE = "hermes_push.json"
READY_FILE = "ready.json"
_SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class PushItem:
    artifact_dir: Path
    files: tuple[Path, ...]
    digest: str


@dataclass(frozen=True)
class PushOutcome:
    item: PushItem
    status: str        # "pushed" | "planned" | "failed"
    detail: str = ""


def _digest(files: Sequence[Path]) -> str:
    h = hashlib.sha256()
    for path in files:
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


def _item_for(folder: Path) -> Optional[PushItem]:
    main = folder / SPEAKERS_JSON
    if not main.is_file():
        return None
    files = [main] + [folder / n for n in OPTIONAL_FILES if (folder / n).is_file()]
    return PushItem(folder, tuple(files), _digest(files))


def _pushed_digest(folder: Path) -> str:
    try:
        data = json.loads((folder / MARKER_FILE).read_text(encoding="utf-8"))
        return str(data.get("digest", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def find_pending(root: Path, *, force: bool = False) -> list[PushItem]:
    """Artifact folders under *root* with something new to send."""
    items = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        item = _item_for(folder)
        if item and (force or _pushed_digest(folder) != item.digest):
            items.append(item)
    return items


def split_target(target: str) -> tuple[str, str]:
    """``user@host:/path`` → (``user@host``, ``/path``). Raises ValueError."""
    host, sep, path = target.partition(":")
    if not sep or not host or not path or "/" in host:
        raise ValueError(f"Expected user@host:/remote/dir, got {target!r}")
    return host, path.rstrip("/") or "/"


def _run(cmd: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        list(cmd), capture_output=True, text=True, timeout=600, check=False,
    )


def _error(proc: "subprocess.CompletedProcess[str]") -> str:
    return (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()


def _transfer(
    item: PushItem, host: str, remote_dir: str, *, runner: Runner, use_rsync: bool,
) -> str:
    """Copy one folder's files, ``ready.json`` last. Returns '' or an error."""
    dest = f"{remote_dir}/{item.artifact_dir.name}"
    ready = item.artifact_dir / READY_FILE
    ready.write_text(json.dumps({
        "folder": item.artifact_dir.name,
        "digest": item.digest,
        "files": [p.name for p in item.files],
        "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }, indent=2), encoding="utf-8")
    try:
        mk = runner(["ssh", *_SSH_OPTS, host, "mkdir", "-p", dest])
        if mk.returncode != 0:
            return _error(mk)
        # Data first, then the completion marker.
        for batch in ([*item.files], [ready]):
            srcs = [str(p) for p in batch]
            if use_rsync:
                cmd = ["rsync", "-t", "--partial", "-e", "ssh " + " ".join(_SSH_OPTS),
                       *srcs, f"{host}:{dest}/"]
            else:
                cmd = ["scp", "-q", *_SSH_OPTS, *srcs, f"{host}:{dest}/"]
            proc = runner(cmd)
            if proc.returncode != 0:
                return _error(proc)
        return ""
    finally:
        ready.unlink(missing_ok=True)


def run_push(
    items: Sequence[PushItem],
    target: str,
    *,
    dry_run: bool = False,
    runner: Runner = _run,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> list[PushOutcome]:
    """Push *items* to *target*; mark each folder on success."""
    host, remote_dir = split_target(target)
    if dry_run:
        return [PushOutcome(item, "planned") for item in items]
    use_rsync = which("rsync") is not None
    outcomes = []
    for item in items:
        try:
            error = _transfer(item, host, remote_dir, runner=runner, use_rsync=use_rsync)
        except (OSError, subprocess.SubprocessError) as exc:
            error = str(exc)
        if error:
            outcomes.append(PushOutcome(item, "failed", error))
            continue
        (item.artifact_dir / MARKER_FILE).write_text(json.dumps({
            "digest": item.digest, "target": target,
            "pushed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, indent=2), encoding="utf-8")
        outcomes.append(PushOutcome(item, "pushed"))
    return outcomes
