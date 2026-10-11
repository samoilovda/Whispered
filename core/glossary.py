"""
Whispered - Term Glossary
Domain terms (AI, programming, psychology, internet slang) the local model
should spell and read correctly, kept in the editable prompts/glossary.md.

A small local model neither knows every term nor tells a garbled
recognition ("чат в джипе") from the real one (ChatGPT). Sending the whole
glossary with every request would cost thousands of prompt tokens and
drown the task, so only the entries whose spelling or a known "heard as"
form occurs in the text at hand are attached (``glossary_block``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from core.logger import get_logger
from core.prompts import load_prompt

logger = get_logger(__name__)

GLOSSARY_PROMPT = "glossary"
# Enough for the terms of one chunk or recording; each entry is ~15-30
# prompt tokens, so the block stays well under ~1k tokens.
MAX_TERMS = 40

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CYRILLIC = re.compile(r"[а-яё]")
# A Russian key may carry a case/number ending ("абьюз" → "абьюзом");
# its own final vowel or soft sign is part of that ending ("травма" →
# "травмы"). Keys shorter than this match as whole words only: a short
# stem plus any ending is too often another word ("имба" → "имбирь",
# "краш" → "крашеный"), so their inflected forms go into "heard as".
_MIN_STEM_KEY = 5
_RU_ENDING = "[а-яё]{0,3}"
_RU_FINAL = re.compile(r"[аеёиоуыэюяйь]$")


@dataclass(frozen=True)
class GlossaryEntry:
    term: str
    variants: tuple[str, ...] = ()
    note: str = ""

    def line(self) -> str:
        """The entry as the model sees it."""
        text = f"- {self.term}"
        if self.variants:
            text += f" (heard as: {', '.join(self.variants)})"
        if self.note:
            text += f" — {self.note}"
        return text


def parse_glossary(text: str) -> tuple[str, list[GlossaryEntry]]:
    """Split prompts/glossary.md into its instruction and its entries.

    The instruction is the text before the first ``##`` heading (HTML
    comments removed); entries are ``- term | heard as, … | note`` lines.
    """
    text = "\n" + _COMMENT.sub("", text)
    head, _, body = text.partition("\n## ")
    intro = head.strip() if body else ""
    entries: list[GlossaryEntry] = []
    for raw in (body or text).splitlines():
        line = raw.strip()
        if not line.startswith("- "):
            continue
        fields = [f.strip() for f in line[2:].split("|")]
        term = fields[0]
        if not term:
            continue
        variants = tuple(
            v.strip() for v in (fields[1] if len(fields) > 1 else "").split(",")
            if v.strip() and v.strip().lower() != term.lower()
        )
        note = fields[2] if len(fields) > 2 else ""
        entries.append(GlossaryEntry(term, variants, note))
    return intro, entries


def _normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


def _key_pattern(key: str) -> str:
    """Regex for one spelling of a term inside lower-cased text: words may
    be joined by a space, a hyphen or nothing ("вайб-кодинг" also finds
    "вайбкодинг"), and a long enough Russian key takes an ending."""
    words = _normalize(key).replace("-", " ").split()
    if not words:
        return ""
    last = words[-1]
    tail = ""
    if _CYRILLIC.search(last) and len(last) >= _MIN_STEM_KEY:
        last = _RU_FINAL.sub("", last)
        tail = _RU_ENDING
    words[-1] = last
    body = r"[\s\-]?".join(re.escape(w) for w in words)
    return rf"(?<!\w){body}{tail}(?!\w)"


@dataclass(frozen=True)
class _Compiled:
    intro: str
    entries: tuple[tuple[GlossaryEntry, re.Pattern[str]], ...]


@lru_cache(maxsize=4)
def _compile(text: str) -> _Compiled:
    intro, entries = parse_glossary(text)
    compiled = []
    for entry in entries:
        keys = [k for k in (entry.term, *entry.variants) if k]
        patterns = [p for p in (_key_pattern(k) for k in keys) if p]
        if patterns:
            compiled.append((entry, re.compile("|".join(patterns))))
    return _Compiled(intro, tuple(compiled))


def select_terms(text: str, glossary_text: str, limit: int = MAX_TERMS) -> list[GlossaryEntry]:
    """Entries of *glossary_text* that occur in *text*, in glossary order,
    at most *limit* of them."""
    haystack = _normalize(text)
    found: list[GlossaryEntry] = []
    for entry, pattern in _compile(glossary_text).entries:
        if pattern.search(haystack):
            found.append(entry)
            if len(found) >= limit:
                logger.debug("Glossary: more than %d terms match; extra ones skipped", limit)
                break
    return found


def glossary_block(text: str, glossary_text: str | None = None) -> str:
    """The glossary instruction plus the entries that occur in *text*, or
    "" when none do (or the glossary is missing) — then the prompt stays
    exactly as it was without a glossary."""
    if glossary_text is None:
        glossary_text = load_prompt(GLOSSARY_PROMPT, fallback="")
    if not glossary_text.strip():
        return ""
    entries = select_terms(text, glossary_text)
    if not entries:
        return ""
    intro = _compile(glossary_text).intro or "GLOSSARY of terms that may occur in the text:"
    return intro + "\n" + "\n".join(e.line() for e in entries)
