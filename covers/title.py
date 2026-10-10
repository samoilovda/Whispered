"""Speaker line helpers for cover titles. Parsing the LLM's title
proposals lives in core/thumb_titles.py."""

from __future__ import annotations

import re

_SPEAKER_JOIN = {
    "ru": "и", "uk": "і", "be": "і", "en": "and", "de": "und",
    "fr": "et", "es": "y", "it": "e", "pt": "e",
}
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def join_speakers(host: str, guest: str, language: str | None = None) -> str:
    """The cover's speaker line ("Денис Самойлов и Евгений Чирков").

    The conjunction follows the recording's language — the cover is in
    that language, not in the app's UI language — and, for a language
    without an entry, the script of the names themselves.
    """
    host, guest = host.strip(), guest.strip()
    if not guest or not host:
        return host or guest
    code = (language or "").split("-")[0].lower()
    word = _SPEAKER_JOIN.get(code) or (
        "и" if _CYRILLIC.search(host + guest) else "and"
    )
    return f"{host} {word} {guest}"
