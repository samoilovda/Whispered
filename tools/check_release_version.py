#!/usr/bin/env python3
"""Reject release filenames/tags that do not match the packaged version."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from version import __version__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    parser.add_argument("--allow-dev", action="store_true")
    args = parser.parse_args()
    if args.allow_dev and args.tag == "dev":
        return 0
    if args.tag not in (__version__, f"v{__version__}"):
        parser.error(f"release tag must be {__version__} or v{__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
