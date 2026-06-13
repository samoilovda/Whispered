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
from typing import Optional

logger = logging.getLogger(__name__)

_LOCALE_DIR = Path(__file__).parent.parent / "locales"
_SUPPORTED = {"en", "ru"}
_STRINGS: dict[str, str] = {}
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


def load_locale(lang: str = "auto") -> None:
    """Load locale strings. Call once at application startup."""
    global _STRINGS, _CURRENT_LANG

    if lang == "auto":
        lang = _detect_system_lang()

    if lang not in _SUPPORTED:
        lang = "en"

    path = _LOCALE_DIR / f"{lang}.json"
    if not path.exists():
        lang = "en"
        path = _LOCALE_DIR / "en.json"

    try:
        with open(path, encoding="utf-8") as f:
            _STRINGS = json.load(f)
        _CURRENT_LANG = lang
    except Exception as exc:
        logger.warning("Failed to load locale file %s: %s", path, exc)
        _STRINGS = {}
        _CURRENT_LANG = "en"


def tr(key: str, **kwargs) -> str:
    """Return the localized string for *key*, filling in {placeholders}.

    Falls back to the key itself when the locale file has no entry.
    """
    text = _STRINGS.get(key, key)
    if kwargs:
        try:
            text = text.format_map(kwargs)
        except (KeyError, ValueError) as exc:
            logger.debug("tr(%r) placeholder substitution failed: %s", key, exc)
    return text


def current_lang() -> str:
    """Return the active language code, e.g. 'en' or 'ru'."""
    return _CURRENT_LANG


# Load default locale eagerly so tr() works even without an explicit call.
load_locale("auto")
