"""Push finished records to the user's own server (Hermes) over SSH.

A record goes once its video is on YouTube (``youtube_upload.json``) and it
has a cleaned text (``clean.md``). Its files are staged in a temporary
folder — where *annotate* may rewrite them, e.g. to mark which speaker is
the user (``application/hermes_speaker.py``) — and copied to
``<target>/<folder name>/`` with ``rsync`` (``scp`` when rsync is not
installed). The files go first and ``ready.json`` last, so a watcher on the
server can treat the arrival of ``ready.json`` as "the folder is complete"
— no polling of this machine and no LLM involved.

State lives in the folder itself (``hermes_push.json``: a digest of what
was sent), so the push is idempotent: nothing is re-sent until one of the
files — or the *salt* (voice profile, name) — changes. Qt-free; the network is touched
only when ``Config.hermes_push_target`` is set (CLAUDE.md, rule 1).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

CLEAN_MD = "clean.md"
UPLOAD_JSON = "youtube_upload.json"   # the video id: the video is on YouTube
SPEAKERS_JSON = "transcript.speakers.json"
SPEAKERS_MD = "transcript.speakers.md"
REQUIRED_FILES = (CLEAN_MD, UPLOAD_JSON)
# Sent when present: the diarized transcript (written by the ``diarize``
# step) and the generated YouTube texts.
OPTIONAL_FILES = (SPEAKERS_JSON, SPEAKERS_MD, "youtube_package.json")
MARKER_FILE = "hermes_push.json"
READY_FILE = "ready.json"
_SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
# Rewrites the staged copies in the given folder (never the record's own
# files); what it returns is added to ready.json.
Annotate = Callable[["PushItem", Path], dict[str, Any]]


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
    info: Optional[dict[str, Any]] = None   # what *annotate* added to ready.json


def _digest(files: Sequence[Path], salt: str = "") -> str:
    h = hashlib.sha256(salt.encode("utf-8"))
    for path in files:
        h.update(path.name.encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


def _item_for(folder: Path, salt: str) -> Optional[PushItem]:
    if not all((folder / n).is_file() for n in REQUIRED_FILES):
        return None
    names = REQUIRED_FILES + tuple(n for n in OPTIONAL_FILES if (folder / n).is_file())
    files = [folder / n for n in names]
    return PushItem(folder, tuple(files), _digest(files, salt))


def _pushed_digest(folder: Path) -> str:
    try:
        data = json.loads((folder / MARKER_FILE).read_text(encoding="utf-8"))
        return str(data.get("digest", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def find_pending(root: Path, *, force: bool = False, salt: str = "") -> list[PushItem]:
    """Artifact folders under *root* with something new to send. *salt*
    goes into the digest: when it changes (a new voice profile), every
    folder is sent again."""
    items = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        item = _item_for(folder, salt)
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
    annotate: Optional[Annotate],
) -> tuple[str, dict[str, Any]]:
    """Copy one folder's files, ``ready.json`` last. Returns ('' or an
    error, what *annotate* added)."""
    dest = f"{remote_dir}/{item.artifact_dir.name}"
    with tempfile.TemporaryDirectory(prefix="hermes-push-") as tmp:
        stage = Path(tmp)
        files = []
        for src in item.files:
            shutil.copy2(src, stage / src.name)
            files.append(stage / src.name)
        info = annotate(item, stage) if annotate else {}
        ready = stage / READY_FILE
        ready.write_text(json.dumps({
            "folder": item.artifact_dir.name,
            "digest": item.digest,
            "files": [p.name for p in files],
            "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **info,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        mk = runner(["ssh", *_SSH_OPTS, host, "mkdir", "-p", dest])
        if mk.returncode != 0:
            return _error(mk), info
        # Data first, then the completion marker.
        for batch in (files, [ready]):
            srcs = [str(p) for p in batch]
            if use_rsync:
                cmd = ["rsync", "-t", "--partial", "-e", "ssh " + " ".join(_SSH_OPTS),
                       *srcs, f"{host}:{dest}/"]
            else:
                cmd = ["scp", "-q", *_SSH_OPTS, *srcs, f"{host}:{dest}/"]
            proc = runner(cmd)
            if proc.returncode != 0:
                return _error(proc), info
        return "", info


def run_push(
    items: Sequence[PushItem],
    target: str,
    *,
    dry_run: bool = False,
    annotate: Optional[Annotate] = None,
    runner: Runner = _run,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> list[PushOutcome]:
    """Push *items* to *target*; mark each folder on success. *annotate*
    runs on each staged copy before it is sent."""
    host, remote_dir = split_target(target)
    if dry_run:
        return [PushOutcome(item, "planned") for item in items]
    use_rsync = which("rsync") is not None
    outcomes = []
    for item in items:
        info: dict[str, Any] = {}
        try:
            error, info = _transfer(item, host, remote_dir, runner=runner,
                              use_rsync=use_rsync, annotate=annotate)
        # A record that fails (unreadable media too) is retried next run.
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            error = str(exc)
        if error:
            outcomes.append(PushOutcome(item, "failed", error))
            continue
        (item.artifact_dir / MARKER_FILE).write_text(json.dumps({
            "digest": item.digest, "target": target,
            "pushed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, indent=2), encoding="utf-8")
        outcomes.append(PushOutcome(item, "pushed", info=info))
    return outcomes
