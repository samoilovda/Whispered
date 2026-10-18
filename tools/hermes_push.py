#!/usr/bin/env python3
"""Send finished records to the Hermes server over SSH (rsync, else scp).

    .venv/bin/python tools/hermes_push.py            # push what is new
    .venv/bin/python tools/hermes_push.py --dry-run  # show what would go

Needs Config.hermes_push_target = "user@host:/path/to/inbox" (empty = off)
and key-based SSH access (no password prompt). Each record folder that has a
speaker transcript (transcript.speakers.json, written by the recipe's
diarize step) goes to <inbox>/<folder>/, with ready.json arriving last —
point a server-side watcher at that file. Folders already sent are skipped
until their transcript or YouTube package changes. Run it from launchd (see
docs/HERMES_PUSH.md). Exit code: 0 ok / nothing to do, 1 a push failed,
2 not configured.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from application.hermes_push import find_pending, run_push  # noqa: E402
from config import get_config  # noqa: E402
from core.paths import output_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="only list what would be sent")
    parser.add_argument("--force", action="store_true", help="send again even if already sent")
    args = parser.parse_args(argv)

    target = get_config().hermes_push_target.strip()
    if not target:
        print("hermes_push_target is empty — set it to user@host:/path/to/inbox in config.json.")
        return 2
    try:
        items = find_pending(output_dir(), force=args.force)
        outcomes = run_push(items, target, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"✗ {exc}")
        return 2
    if not outcomes:
        print("Nothing new to send.")
        return 0
    failed = False
    for outcome in outcomes:
        name = outcome.item.artifact_dir.name
        if outcome.status == "pushed":
            print(f"✓ {name}")
        elif outcome.status == "planned":
            print(f"→ would send {name} ({len(outcome.item.files)} files)")
        else:
            failed = True
            print(f"✗ {name}: {outcome.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
