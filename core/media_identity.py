"""Is this the same recording? (UI/UX audit 2026-10-18, UX-09 / T09.)

A record keeps the path its media had when it was transcribed. When that
file is moved or renamed, the user points the record at it again; before
accepting, we check it is the same recording. A full content hash of a
multi-gigabyte video is too slow, so the fingerprint covers the size plus
the first and last MiB — enough to tell two different recordings of the
same length apart. Records made before fingerprints existed can only be
compared by duration, which is reported as "likely", never as "same".
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional

SAME = "same"            # fingerprints match
LIKELY = "likely"        # no stored fingerprint; durations agree
DIFFERENT = "different"  # fingerprints or durations disagree
UNKNOWN = "unknown"      # nothing to compare with

_CHUNK = 1 << 20
_DURATION_TOLERANCE = 1.5


def media_fingerprint(path: str) -> str:
    """``v1:<size>:<sha256 of the first and last MiB>``. Raises OSError."""
    size = os.path.getsize(path)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read(_CHUNK))
        if size > _CHUNK:
            handle.seek(max(_CHUNK, size - _CHUNK))
            digest.update(handle.read(_CHUNK))
    return f"v1:{size}:{digest.hexdigest()[:32]}"


def compare_media(
    stored_fingerprint: str, stored_duration: Optional[float],
    fingerprint: str, duration: Optional[float],
) -> str:
    """How a candidate file relates to the record's original media."""
    if stored_fingerprint and fingerprint:
        return SAME if stored_fingerprint == fingerprint else DIFFERENT
    if duration is None or not stored_duration:
        return UNKNOWN
    if abs(duration - stored_duration) <= _DURATION_TOLERANCE:
        return LIKELY
    return DIFFERENT
