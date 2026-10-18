"""What went wrong in a step, in the user's terms (UI/UX audit 2026-10-18,
UX-11 / T11).

A failed step carries the raw exception text — often an English stack
message from a library. ``classify_failure()`` maps it to a small set of
kinds the run screen can explain and offer a fix for; the raw text stays
available behind "Details", with secrets redacted.
"""

from __future__ import annotations

import re

NEEDS_SETUP = "needs_setup"                    # a key, a model or a prompt file is missing
SOURCE_MISSING = "source_missing"              # the media/transcript the step reads is gone
PROVIDER_UNAVAILABLE = "provider_unavailable"  # LM Studio / the cloud provider did not answer
INVALID_OUTPUT = "invalid_output"              # the model answered with something unusable
IO_ERROR = "io_error"                          # could not read/write a file
UNKNOWN = "unknown"

FAILURE_KINDS = (NEEDS_SETUP, SOURCE_MISSING, PROVIDER_UNAVAILABLE, INVALID_OUTPUT, IO_ERROR, UNKNOWN)

# First match wins; patterns are lower-case and deliberately conservative.
_RULES: tuple[tuple[str, str], ...] = (
    (PROVIDER_UNAVAILABLE, r"not responding|connection (refused|reset|error|aborted)|"
                           r"timed? ?out|timeout|failed to establish|max retries|"
                           r"service unavailable|\b50[234]\b|no response|unreachable"),
    (NEEDS_SETUP, r"api key|apikey|unauthori[sz]ed|\b401\b|\b403\b|no model|model .*not (loaded|found)|"
                  r"prompt file is required|not installed|not configured|ffmpeg not found"),
    (SOURCE_MISSING, r"source .*(missing|not found)|no such file .*(\.mp[34]|\.wav|\.m4a|\.mov|\.mkv)|"
                     r"media .*not found|video .*not found"),
    (IO_ERROR, r"permission denied|no space left|read-only file system|disk full|"
               r"errno|is a directory|no such file or directory"),
    (INVALID_OUTPUT, r"json|could not parse|unparsable|empty (response|result|answer)|"
                     r"produced no|no complete result|finish_reason|invalid output"),
)

# Replaced whole.
_SECRETS = (
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"ya29\.[0-9A-Za-z_\-]+"),
)
# The label (group 1) is kept, the value replaced.
_LABELLED_SECRETS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s&'\",]+"),
)


def classify_failure(error: str) -> str:
    """The kind of failure *error* (a step's raw error text) describes."""
    text = (error or "").lower()
    for kind, pattern in _RULES:
        if re.search(pattern, text):
            return kind
    return UNKNOWN


def redact_secrets(text: str) -> str:
    """*text* with anything key- or token-shaped replaced by ``•••``."""
    out = text or ""
    for pattern in _LABELLED_SECRETS:
        out = pattern.sub(lambda m: m.group(1) + "•••", out)
    for pattern in _SECRETS:
        out = pattern.sub("•••", out)
    return out
