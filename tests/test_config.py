"""Tests for config load/save and validation, isolated from the real home dir."""

import json

import config
from config import Config


def _isolate(monkeypatch, tmp_path):
    """Point the config module at a temporary file."""
    cfg_dir = tmp_path / ".whisper-fedora"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_dir / "config.json")


def test_save_load_roundtrip(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = Config(lm_studio_url="http://example:9999/v1", lm_request_timeout=120)
    assert c.save()

    loaded = Config.load()
    assert loaded.lm_studio_url == "http://example:9999/v1"
    assert loaded.lm_request_timeout == 120


def test_load_missing_file_returns_defaults(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    loaded = Config.load()
    assert loaded.lm_studio_url == "http://localhost:1234/v1"


def test_load_ignores_unknown_fields(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.CONFIG_FILE.write_text(
        json.dumps({"lm_studio_url": "http://x/v1", "bogus_field": 42}),
        encoding="utf-8",
    )
    loaded = Config.load()
    assert loaded.lm_studio_url == "http://x/v1"
    assert not hasattr(loaded, "bogus_field")


def test_load_corrupt_file_returns_defaults(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.CONFIG_FILE.write_text("{ not valid json", encoding="utf-8")
    loaded = Config.load()
    assert loaded.lm_studio_url == "http://localhost:1234/v1"


def test_has_hf_token():
    assert not Config().has_hf_token()
    assert not Config(hf_token="short").has_hf_token()
    assert Config(hf_token="hf_aLongEnoughTokenValue").has_hf_token()
