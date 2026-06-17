"""Tests for core/i18n.py — locale loading and tr() helper."""

import json
import pathlib
import sys
import types

# core/__init__.py imports Qt-dependent submodules; stub them before loading.
for _mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui",
             "PyQt6.QtMultimedia"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

_lm_stub = types.ModuleType("core.lm_client")
_lm_stub.LMStudioClient = object
_lm_stub.DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
sys.modules.setdefault("core.lm_client", _lm_stub)

_ai_stub = types.ModuleType("core.ai_worker")
_ai_stub.AIProcessingWorker = object
sys.modules.setdefault("core.ai_worker", _ai_stub)

import pytest  # noqa: E402
from core.i18n import load_locale, tr, current_lang   # noqa: E402


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadLocale:
    def test_load_en(self):
        load_locale("en")
        assert current_lang() == "en"

    def test_load_ru(self):
        load_locale("ru")
        assert current_lang() == "ru"

    def test_unsupported_falls_back_to_en(self):
        load_locale("zz")
        assert current_lang() == "en"

    def test_auto_does_not_crash(self):
        load_locale("auto")
        assert current_lang() in ("en", "ru")


class TestTr:
    def setup_method(self):
        load_locale("en")

    def test_known_key_returns_string(self):
        result = tr("btn_transcribe")
        assert isinstance(result, str) and len(result) > 0

    def test_unknown_key_returns_key(self):
        assert tr("__no_such_key__") == "__no_such_key__"

    def test_placeholder_substitution(self):
        result = tr("toast_complete", words=42)
        assert "42" in result

    def test_multiple_placeholders(self):
        result = tr("status_complete", words=10, seconds=5)
        assert "10" in result and "5" in result

    def test_missing_placeholder_does_not_crash(self):
        # {words} not supplied — should not raise
        result = tr("toast_complete")
        assert isinstance(result, str)

    def test_ru_key_differs_from_en(self):
        load_locale("en")
        en = tr("btn_transcribe")
        load_locale("ru")
        ru = tr("btn_transcribe")
        assert en != ru

    def test_ru_placeholder(self):
        load_locale("ru")
        result = tr("toast_complete", words=7)
        assert "7" in result

    def test_app_title_same_in_both_locales(self):
        for lang in ("en", "ru"):
            load_locale(lang)
            assert tr("app_title") == "Whispered"


class TestLocaleFiles:
    _base = pathlib.Path(__file__).parent.parent / "locales"

    def test_all_en_values_non_empty(self):
        data = json.loads((self._base / "en.json").read_text(encoding="utf-8"))
        for key, val in data.items():
            assert isinstance(val, str) and len(val) > 0, f"Empty value: {key}"

    def test_ru_has_same_keys_as_en(self):
        en_keys = set(json.loads((self._base / "en.json").read_text(encoding="utf-8")))
        ru_keys = set(json.loads((self._base / "ru.json").read_text(encoding="utf-8")))
        missing = en_keys - ru_keys
        assert not missing, f"Keys in en.json missing from ru.json: {missing}"

    def test_all_ru_values_non_empty(self):
        data = json.loads((self._base / "ru.json").read_text(encoding="utf-8"))
        for key, val in data.items():
            assert isinstance(val, str) and len(val) > 0, f"Empty value: {key}"
