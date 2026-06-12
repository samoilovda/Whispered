"""Tests for config.py — no Qt, no network required."""
import json
import os
import sys
import types
from pathlib import Path
import pytest

# Stub Qt and heavy core imports before config pulls them in through core/__init__
for _mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

# Stub the Qt-dependent parts of core so config.py can import core.logger
_core_stub = types.ModuleType("core")
_core_stub.get_logger = lambda name: __import__("logging").getLogger(name)
_core_stub.setup_logging = lambda *a, **k: None
sys.modules.setdefault("core", _core_stub)
sys.modules.setdefault("core.logger", types.ModuleType("core.logger"))
sys.modules["core.logger"].get_logger = lambda name: __import__("logging").getLogger(name)
sys.modules["core.logger"].setup_logging = lambda *a, **k: None
sys.modules.setdefault("core.ai_worker", types.ModuleType("core.ai_worker"))
sys.modules.setdefault("core.lm_client", types.ModuleType("core.lm_client"))

import config as config_module
from config import Config


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config storage to a temp directory for every test."""
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config_module, "_config", None)
    yield
    monkeypatch.setattr(config_module, "_config", None)


class TestConfigDefaults:
    def test_default_lm_url(self):
        cfg = Config()
        assert cfg.lm_studio_url == "http://localhost:1234/v1"

    def test_default_theme(self):
        cfg = Config()
        assert cfg.theme == "dark"

    def test_default_diarization_disabled(self):
        cfg = Config()
        assert not cfg.diarization_enabled

    def test_has_hf_token_false_when_none(self):
        cfg = Config()
        assert not cfg.has_hf_token()

    def test_has_hf_token_false_when_short(self):
        cfg = Config(hf_token="abc")
        assert not cfg.has_hf_token()

    def test_has_hf_token_true(self):
        cfg = Config(hf_token="hf_" + "x" * 40)
        assert cfg.has_hf_token()


class TestConfigRoundTrip:
    def test_save_and_load(self, tmp_path):
        cfg = Config(
            lm_studio_url="http://192.168.1.1:5000/v1",
            theme="light",
            diarization_enabled=True,
            default_num_speakers=3,
            book_temperature=0.7,
        )
        cfg.save()
        loaded = Config.load()
        assert loaded.lm_studio_url == "http://192.168.1.1:5000/v1"
        assert loaded.theme == "light"
        assert loaded.diarization_enabled is True
        assert loaded.default_num_speakers == 3
        assert abs(loaded.book_temperature - 0.7) < 1e-9

    def test_load_missing_file_returns_defaults(self):
        loaded = Config.load()
        assert loaded.theme == "dark"

    def test_unknown_fields_ignored(self, tmp_path):
        cfg_file = config_module.CONFIG_FILE
        data = {"theme": "light", "future_field_xyz": "value", "lm_studio_url": "http://x/v1"}
        cfg_file.write_text(json.dumps(data), encoding="utf-8")
        loaded = Config.load()
        assert loaded.theme == "light"
        assert loaded.lm_studio_url == "http://x/v1"
        # future_field_xyz should simply be ignored
        assert not hasattr(loaded, "future_field_xyz")

    def test_corrupted_json_returns_defaults(self, tmp_path):
        config_module.CONFIG_FILE.write_text("{not valid json", encoding="utf-8")
        loaded = Config.load()
        assert loaded.theme == "dark"


class TestGlobalHelpers:
    def test_get_config_singleton(self):
        from config import get_config
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_save_config_writes_file(self):
        from config import get_config, save_config
        cfg = get_config()
        cfg.theme = "light"
        result = save_config()
        assert result is True
        assert config_module.CONFIG_FILE.exists()
        data = json.loads(config_module.CONFIG_FILE.read_text())
        assert data["theme"] == "light"

    def test_reset_config(self):
        from config import get_config, reset_config
        cfg = get_config()
        cfg.theme = "light"
        reset = reset_config()
        assert reset.theme == "dark"
