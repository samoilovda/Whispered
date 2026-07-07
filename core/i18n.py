"""
Whispered – i18n (internationalization)
JSON-based locale loader with a simple tr() helper.

Usage:
    from core.i18n import tr, load_locale
    load_locale("ru")        # called once at startup
    label.setText(tr("btn_transcribe"))
    msg = tr("toast_complete", words=42)
"""

from __future__ import annotations

import json
import locale
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCALE_DIR = Path(__file__).parent.parent / "locales"
_SUPPORTED = {"en", "ru"}
_STRINGS: dict[str, str] = {}
_EN_STRINGS: dict[str, str] = {}   # English fallback loaded once at startup
_CURRENT_LANG: str = "en"


def _detect_system_lang() -> str:
    """Detect system language; returns 'ru' or 'en'."""
    for var in ("LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES"):
        val = os.environ.get(var, "")
        if val.lower().startswith("ru"):
            return "ru"
    try:
        lang, _ = locale.getlocale()
        if lang and lang.lower().startswith("ru"):
            return "ru"
    except Exception as exc:
        # locale.getlocale() can raise on misconfigured systems; default to 'en'
        logger.debug("locale detection failed: %s", exc)
    return "en"


def _load_json(path: Path) -> dict[str, str]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to load locale file %s: %s", path, exc)
        return {}


def load_locale(lang: str = "auto") -> None:
    """Load locale strings. Call once at application startup.

    Always loads English into _EN_STRINGS as a fallback so that keys
    missing from a non-English locale degrade to the English string
    rather than to the bare key.
    """
    global _STRINGS, _EN_STRINGS, _CURRENT_LANG

    if lang == "auto":
        lang = _detect_system_lang()

    if lang not in _SUPPORTED:
        lang = "en"

    en_path = _LOCALE_DIR / "en.json"
    _EN_STRINGS = _load_json(en_path)

    if lang == "en":
        _STRINGS = _EN_STRINGS
    else:
        path = _LOCALE_DIR / f"{lang}.json"
        if not path.exists():
            lang = "en"
            _STRINGS = _EN_STRINGS
        else:
            _STRINGS = _load_json(path) or _EN_STRINGS

    _CURRENT_LANG = lang


def tr(key: str, **kwargs) -> str:
    """Return the localized string for *key*, filling in {placeholders}.

    Lookup order: current language → English → bare key.
    """
    text = _STRINGS.get(key) or _EN_STRINGS.get(key, key)
    if kwargs:
        try:
            text = text.format_map(kwargs)
        except (KeyError, ValueError) as exc:
            logger.debug("tr(%r) placeholder substitution failed: %s", key, exc)
    return text


def current_lang() -> str:
    """Return the active language code, e.g. 'en' or 'ru'."""
    return _CURRENT_LANG


# Eager load at import time so tr() returns real strings immediately — even
# when main.py hasn't called load_locale() yet (unit tests, early startup).
# The cost is two small JSON reads at first import; acceptable given the file
# sizes (~50 KB each) and that it happens once per process.
load_locale("auto")
