"""Forgiving text matching for search-as-you-type lists (the Ctrl+K palette).

Every query word must be found in the text: as the start of a word
("зап" → "Новая запись"), inside a word, as word initials ("нз" →
"Новая запись") or as letters in order ("экпрт" → "Экспорт"). Scores
rank those in that order; ``None`` means no match.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _initials(text: str) -> str:
    return "".join(word[0] for word in _WORD_RE.findall(text))


def _subsequence(needle: str, hay: str) -> bool:
    it = iter(hay)
    return all(ch in it for ch in needle)


def match_score(query: str, text: str) -> float | None:
    """Score how well *query* matches *text* (higher is better), or
    ``None`` when it doesn't. An empty query matches everything with 0."""
    tokens = query.casefold().split()
    if not tokens:
        return 0.0
    hay = text.casefold()
    words = _WORD_RE.findall(hay)
    initials = _initials(hay)
    score = 0.0
    for token in tokens:
        if any(word.startswith(token) for word in words):
            score += 3.0 + (1.0 if hay.startswith(token) else 0.0)
        elif token in hay:
            score += 2.0
        elif len(token) > 1 and token in initials:
            score += 1.5
        elif len(token) > 2 and _subsequence(token, hay):
            score += 0.5
        else:
            return None
    # Shorter texts with the same hits are closer matches.
    return score - len(hay) / 1000.0
