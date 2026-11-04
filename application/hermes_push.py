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
only when ``Config.hermes_push_target`` is set (AGENTS.md, rule 1).
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Collection, Optional, Sequence

CLEAN_MD = "clean.md"
UPLOAD_JSON = "youtube_upload.json"   # the video id: the video is on YouTube
SPEAKERS_JSON = "transcript.speakers.json"
SPEAKERS_MD = "transcript.speakers.md"
# The plain timed transcript, for records that were never diarized: without
# it the server has no timecodes to quote from (clean.md has none).
TIMED_JSON = "transcript.timed.json"
REQUIRED_FILES = (CLEAN_MD, UPLOAD_JSON)
# Sent when present: the diarized transcript (written by the ``diarize``
# step) or the timed one, and the generated YouTube texts.
OPTIONAL_FILES = (SPEAKERS_JSON, SPEAKERS_MD, TIMED_JSON, "youtube_package.json")
_RECORD_ID_RE = re.compile(r"-(\d+)$")
MARKER_FILE = "hermes_push.json"
READY_FILE = "ready.json"
_SSH_OPTS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
_SENT_MODE = 0o640
# What a remote path may contain. ssh hands its command to the server's
# shell, and scp/rsync (macOS ships rsync 2.6.9, which does not protect its
# arguments) do the same with the destination, so a space, quote or $(...)
# in a folder name would split the path or run on the server.
_UNSAFE_REMOTE = re.compile(r"[^\w.\-]")
_SAFE_REMOTE_DIR = re.compile(r"^[\w./~\-]+$")

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


def write_timed_transcripts(
    root: Path, payload_for: Callable[[int], Optional[dict[str, Any]]]
) -> list[Path]:
    """For every record that would be sent but has no speaker transcript,
    write ``transcript.timed.json`` (start/end/text of each segment) from its
    history payload. *payload_for* maps the record id — the number that ends
    the folder name (``core.paths.artifact_dir``) — to that payload. Rewrites
    only when the content changed, so the push digest stays put. Returns the
    files written."""
    written = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if not all((folder / n).is_file() for n in REQUIRED_FILES):
            continue
        if (folder / SPEAKERS_JSON).is_file():
            continue
        match = _RECORD_ID_RE.search(folder.name)
        payload = payload_for(int(match.group(1))) if match else None
        segments = [
            {"start": float(seg["start"]), "end": float(seg["end"]), "text": str(seg["text"]).strip()}
            for seg in (payload or {}).get("segments") or []
            if isinstance(seg, dict) and str(seg.get("text") or "").strip()
            and isinstance(seg.get("start"), (int, float))
            and isinstance(seg.get("end"), (int, float))
        ]
        if not segments:
            continue
        text = json.dumps({
            "language": (payload or {}).get("language", ""),
            "duration": (payload or {}).get("duration", 0),
            "segments": segments,
        }, ensure_ascii=False, indent=1)
        target = folder / TIMED_JSON
        try:
            if target.read_text(encoding="utf-8") == text:
                continue
        except OSError:
            pass
        target.write_text(text, encoding="utf-8")
        written.append(target)
    return written


def find_pending(
    root: Path, *, force: bool = False, salt: str = "",
    live_ids: Optional[Collection[int]] = None,
) -> list[PushItem]:
    """Artifact folders under *root* with something new to send. *salt*
    goes into the digest: when it changes (a new voice profile), every
    folder is sent again. With *live_ids* (``records.live_record_ids()``),
    folders of deleted records are left out."""
    from application.records import folder_record_id, is_live

    items = []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        if not is_live(folder_record_id(folder), live_ids):
            continue
        item = _item_for(folder, salt)
        if item and (force or _pushed_digest(folder) != item.digest):
            items.append(item)
    return items


def split_target(target: str) -> tuple[str, str]:
    """``user@host:/path`` → (``user@host``, ``/path``). Raises ValueError,
    also for a remote dir with characters a remote shell would interpret."""
    host, sep, path = target.partition(":")
    if not sep or not host or not path or "/" in host:
        raise ValueError(f"Expected user@host:/remote/dir, got {target!r}")
    if not _SAFE_REMOTE_DIR.match(path) or host.startswith("-"):
        raise ValueError(
            f"The remote dir may only contain letters, digits and . _ - / ~, got {path!r}")
    return host, path.rstrip("/") or "/"


def remote_folder_name(folder: Path) -> str:
    """The folder's name on the server: its local name with every character
    a shell could interpret (spaces, quotes, ``$``, ``;``…) replaced by ``_``.
    Names without such characters stay as they are."""
    return _UNSAFE_REMOTE.sub("_", folder.name).strip(".") or "record"


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
    remote_name = remote_folder_name(item.artifact_dir)
    dest = f"{remote_dir}/{remote_name}"
    with tempfile.TemporaryDirectory(prefix="hermes-push-") as tmp:
        stage = Path(tmp)
        files = []
        for src in item.files:
            shutil.copy2(src, stage / src.name)
            files.append(stage / src.name)
        # The data dir is private (0600), and rsync/scp carry the mode over:
        # the server's reader shares only the group.
        for path in files:
            path.chmod(_SENT_MODE)
        info = annotate(item, stage) if annotate else {}
        ready = stage / READY_FILE
        ready.write_text(json.dumps({
            "folder": remote_name,
            "local_folder": item.artifact_dir.name,
            "digest": item.digest,
            "files": [p.name for p in files],
            "sent_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **info,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        mk = runner(["ssh", *_SSH_OPTS, host, "mkdir", "-p", "--", shlex.quote(dest)])
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
