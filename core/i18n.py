"""
Whispered – i18n (internationalization)
JSON-based locale loader with a simple tr() helper.

Usage:
    from core.i18n import tr, load_locale
    load_locale("ru")        # called once at startup
    label.setText(tr("btn_transcribe"))
    msg = tr("toast_complete", words=42)

Hot language switching:
    set_locale("ru")         # at runtime, from the Settings dialog
    on_language_changed(self._retranslate)   # in a widget's __init__

``set_locale()`` reloads the strings and then invokes every callback
registered through ``on_language_changed()`` so open widgets can re-pull
their captions without an application restart. Callbacks are held weakly
(pass a bound method or keep your own reference); a dead one is dropped
silently and one that raises is logged and skipped so a single broken
widget can't abort the rest of the fan-out.
"""

from __future__ import annotations

import json
import locale
import logging
import os
import weakref
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# Language-change subscribers. Bound methods are stored as ``WeakMethod`` and
# plain functions as ``weakref.ref`` so a destroyed widget drops out of the
# fan-out on its own (Qt gives us no deterministic teardown hook here).
_LANG_SUBSCRIBERS: "list[weakref.ref[Callable[[], None]]]" = []

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


# ---------------------------------------------------------------------------
# Hot language switching
# ---------------------------------------------------------------------------

def on_language_changed(callback: Callable[[], None]) -> None:
    """Register *callback* to run whenever ``set_locale()`` changes the
    active language.

    The reference is weak: pass a bound method (``self._retranslate``) or
    otherwise keep the callable alive yourself. Registering the same
    callback twice is a no-op.
    """
    try:
        ref: "weakref.ref[Callable[[], None]]" = weakref.WeakMethod(callback)  # type: ignore[arg-type]
    except TypeError:
        ref = weakref.ref(callback)

    for existing in _LANG_SUBSCRIBERS:
        if existing() is not None and existing() == callback:
            return
    _LANG_SUBSCRIBERS.append(ref)


def remove_language_listener(callback: Callable[[], None]) -> None:
    """Unregister a callback added via :func:`on_language_changed`."""
    _LANG_SUBSCRIBERS[:] = [
        r for r in _LANG_SUBSCRIBERS if r() is not None and r() != callback
    ]


def _notify_language_changed() -> None:
    for ref in list(_LANG_SUBSCRIBERS):
        cb = ref()
        if cb is None:
            try:
                _LANG_SUBSCRIBERS.remove(ref)
            except ValueError:
                pass
            continue
        try:
            cb()
        except Exception:
            logger.exception("language-change listener failed: %r", cb)


def set_locale(lang: str) -> bool:
    """Switch the active UI language at runtime.

    Reloads the locale strings and, if the effective language actually
    changed, fires every :func:`on_language_changed` listener. Returns
    ``True`` when a change was broadcast.
    """
    previous = _CURRENT_LANG
    load_locale(lang)
    if _CURRENT_LANG == previous:
        return False
    _notify_language_changed()
    return True


# Eager load at import time so tr() returns real strings immediately — even
# when main.py hasn't called load_locale() yet (unit tests, early startup).
# The cost is two small JSON reads at first import; acceptable given the file
# sizes (~50 KB each) and that it happens once per process.
load_locale("auto")
