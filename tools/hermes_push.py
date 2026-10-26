#!/usr/bin/env python3
"""Send finished records to the Hermes server over SSH (rsync, else scp).

    .venv/bin/python tools/hermes_push.py            # push what is new
    .venv/bin/python tools/hermes_push.py --dry-run  # show what would go
    .venv/bin/python tools/hermes_push.py --enroll <record folder> "Speaker 1"

Needs Config.hermes_push_target = "user@host:/path/to/inbox" (empty = off)
and key-based SSH access (no password prompt). Each record folder whose video
is on YouTube (youtube_upload.json) and that has a cleaned text (clean.md)
goes to <inbox>/<folder>/ with its speaker transcript and YouTube package
when present, ready.json arriving last — point a server-side watcher at that
file. In the speaker transcript the user's voice is found against the voice
profile made by --enroll and named Config.cover_host_name; ready.json's "me"
says which speaker it is, or why none was marked. Folders already sent are
skipped until something in them (or the profile) changes. Run it from launchd
(see docs/HERMES_PUSH.md). Exit code: 0 ok / nothing to do, 1 a push failed,
2 not configured.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from application import hermes_speaker as hs  # noqa: E402
from application.hermes_push import SPEAKERS_JSON, find_pending, run_push  # noqa: E402
from config import get_config  # noqa: E402
from core.paths import data_dir, output_dir  # noqa: E402

PROFILE_NAME = "voice_profile.json"


class _Voice:
    """Speaker embeddings of one media file at a time (lazy: the models and
    the audio load only when a record with a speaker transcript is sent)."""

    def __init__(self) -> None:
        from diarizer import Diarizer

        self._diarizer = Diarizer()
        self._paths: Any = None

    @property
    def model(self) -> str:
        from core.model_manifest import MANIFEST

        return MANIFEST["diarization-embedding"].filename

    def embedder(self, media: Path) -> Callable[[Sequence[hs.Window]], list[Any]]:
        from diarizer import SAMPLE_RATE, load_samples

        if self._paths is None:
            self._paths = self._diarizer.ensure_models()
        samples = load_samples(str(media))

        def embed(windows: Sequence[hs.Window]) -> list[Any]:
            return [
                self._diarizer._embed(self._paths, samples[int(a * SAMPLE_RATE):int(b * SAMPLE_RATE)])
                for a, b in windows if b > a
            ]

        return embed


def _enroll(folder: Path, speaker: str, profile_path: Path) -> int:
    try:
        speakers = json.loads((folder / SPEAKERS_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"✗ {folder / SPEAKERS_JSON}: {exc}")
        return 2
    labels = speakers.get("speaker_names") or {}
    # Accept the shown name ("Денис") as well as the raw label ("Speaker 1").
    sid = next((k for k, v in labels.items() if v == speaker), speaker)
    windows = hs.speaker_windows(speakers.get("segments") or [], sid, limit=40)
    media = hs.source_path(folder)
    if not windows or media is None:
        print(f"✗ no long turns of {speaker!r}" if not windows else "✗ source media not found")
        return 2
    voice = _Voice()
    vec = hs.centroid(voice.embedder(media)(windows))
    assert vec is not None
    hs.save_profile(profile_path, hs.VoiceProfile(vec, voice.model, f"{folder.name} / {sid}"))
    print(f"✓ voice profile from {len(windows)} turns of {sid} → {profile_path}")
    return 0


def _me_note(info: dict[str, Any]) -> str:
    me = info.get("me") or {}
    if me.get("speaker_id"):
        return f" — {me['speaker_id']} = {me['name']} (voice {me['score']:.2f} vs {me['runner_up']:.2f})"
    return f" — speaker not marked: {me['reason']}" if me.get("reason") else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="only list what would be sent")
    parser.add_argument("--force", action="store_true", help="send again even if already sent")
    parser.add_argument("--enroll", nargs=2, metavar=("FOLDER", "SPEAKER"),
                        help="make the voice profile from SPEAKER's turns in a record folder")
    args = parser.parse_args(argv)

    profile_path = data_dir() / PROFILE_NAME
    if args.enroll:
        return _enroll(Path(args.enroll[0]).expanduser(), args.enroll[1], profile_path)

    config = get_config()
    target = config.hermes_push_target.strip()
    if not target:
        print("hermes_push_target is empty — set it to user@host:/path/to/inbox in config.json.")
        return 2
    profile = hs.load_profile(profile_path)
    name = config.cover_host_name.strip() or "me"
    salt = f"{profile.fingerprint() if profile else ''}|{name}"
    try:
        items = find_pending(output_dir(), force=args.force, salt=salt)
        voice = _Voice() if items and not args.dry_run else None
        annotate = hs.annotator(profile, name, voice.model, voice.embedder) if voice else None
        outcomes = run_push(items, target, dry_run=args.dry_run, annotate=annotate)
    except ValueError as exc:
        print(f"✗ {exc}")
        return 2
    if not outcomes:
        print("Nothing new to send.")
        return 0
    if profile is None:
        print("! no voice profile — speakers are sent unmarked (see --enroll)")
    failed = False
    for outcome in outcomes:
        folder = outcome.item.artifact_dir.name
        if outcome.status == "pushed":
            print(f"✓ {folder}{_me_note(outcome.info or {})}")
        elif outcome.status == "planned":
            print(f"→ would send {folder} ({len(outcome.item.files)} files)")
        else:
            failed = True
            print(f"✗ {folder}: {outcome.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
