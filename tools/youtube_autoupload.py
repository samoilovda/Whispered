#!/usr/bin/env python3
"""Upload every record queued in the YouTube publish wizard.

    .venv/bin/python tools/youtube_autoupload.py            # upload the queue
    .venv/bin/python tools/youtube_autoupload.py --dry-run  # show what would go

A record is queued with "Queue for upload" on the wizard's last step,
after its texts and cover were approved. Uses the YouTube connection made
in Settings → YouTube (OAuth client + login in the OS keyring); nothing is
sent anywhere else. Interrupted uploads resume on the next run; records
already on YouTube are skipped. Exit code: 0 all done, 1 something failed,
2 not connected.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from application.youtube_autoupload import (  # noqa: E402
    UPLOADS_PER_DAY,
    QueuedUpload,
    find_queued,
    run_queue,
)
from core.paths import output_dir  # noqa: E402


def _mark_in_library(item: QueuedUpload) -> None:
    """Same bookkeeping as the app after an upload: the record lists a
    YouTube upload among its artifacts."""
    if item.record_id is None:
        return
    try:
        from core.history import get_history_store

        store = get_history_store()
        current = store.get_record(item.record_id) or {}
        artifacts = {"transcript", "youtube_upload", *current.get("artifacts", [])}
        store.set_artifacts(item.record_id, sorted(artifacts))
    except Exception as exc:
        print(f"  (library not updated: {exc})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="only list what would be uploaded")
    parser.add_argument("--max", type=int, default=UPLOADS_PER_DAY,
                        help=f"uploads per run (default {UPLOADS_PER_DAY}, the daily API quota)")
    parser.add_argument("--record", type=int, help="only this library record id")
    parser.add_argument("--force", action="store_true", help="upload again even if already uploaded")
    args = parser.parse_args(argv)

    items = find_queued(output_dir())
    if args.record is not None:
        items = [item for item in items if item.record_id == args.record]
    if not items:
        print("Nothing is queued. Use \"Queue for upload\" on the publish wizard's last step.")
        return 0

    uploader = None
    if not args.dry_run:
        from config import get_config
        from core import youtube_oauth
        from core.youtube_upload import UploadClient

        cfg = get_config()
        # Say what is missing: the OAuth client (its secret can be lost on
        # its own, e.g. a keyring entry removed) or the account login.
        where = "Settings → AI / LM Studio → YouTube account"
        if not (cfg.yt_oauth_client_id and cfg.yt_oauth_client_secret):
            print(f"YouTube is not connected: the OAuth client is missing — {where} → "
                  "\"Import client_secret.json…\".")
            return 2
        if not youtube_oauth.is_connected():
            print(f"YouTube is not connected: nobody is signed in — {where} → \"Connect account\".")
            return 2
        uploader = UploadClient(youtube_oauth.TokenProvider(
            cfg.yt_oauth_client_id, cfg.yt_oauth_client_secret))

    last_percent: dict[Path, int] = {}

    def progress(item: QueuedUpload, sent: int, total: int) -> None:
        percent = int(sent * 100 / total) if total else 100
        if percent >= last_percent.get(item.artifact_dir, -10) + 10:
            last_percent[item.artifact_dir] = percent
            print(f"  {percent:3d}%  {sent / 1e6:.0f} / {total / 1e6:.0f} MB", flush=True)

    def uploaded(item: QueuedUpload, record) -> None:
        _mark_in_library(item)

    for item in items:
        print(f"• {item.package.title}  [{item.package.privacy}]  {item.package.video_path.name}")
    outcomes = run_queue(items, uploader, limit=args.max, force=args.force,
                         on_progress=progress, on_uploaded=uploaded)
    failed = False
    for outcome in outcomes:
        title = outcome.item.package.title
        if outcome.status == "uploaded":
            print(f"✓ {title}: https://studio.youtube.com/video/{outcome.video_id}/edit")
            if outcome.detail:
                print(f"  cover not set: {outcome.detail}")
        elif outcome.status == "planned":
            cover = outcome.item.package.thumbnail_path
            print(f"→ would upload {title} (cover: {cover.name if cover else 'none'})")
        elif outcome.status == "skipped":
            suffix = f" ({outcome.video_id})" if outcome.video_id else ""
            print(f"– {title}: {outcome.detail}{suffix}")
        else:
            failed = True
            print(f"✗ {title}: {outcome.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
