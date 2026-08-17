"""Tests for core/secrets_store.py — no Qt, no real keyring required."""
import pytest

from core.secrets_store import SecretReadResult, read_secret


class _RaisingKeyring:
    """Fake keyring backend that always raises on reads."""

    def get_password(self, service, name):
        raise RuntimeError("backend unavailable")

    def set_password(self, service, name, value):
        pass

    def delete_password(self, service, name):
        pass


class _EmptyKeyring:
    """Fake keyring backend that has no entries."""

    def get_password(self, service, name):
        return None


class _FilledKeyring:
    """Fake keyring backend with a stored secret."""

    def __init__(self, store=None):
        self._store = store or {}

    def get_password(self, service, name):
        return self._store.get(name)


def _patch_keyring(monkeypatch, backend):
    """Patch _keyring_module() to return the given backend object."""
    import core.secrets_store as ss_module
    monkeypatch.setattr(ss_module, "_keyring_module", lambda: backend)


class TestReadSecretTriState:
    def test_found_when_entry_exists(self, monkeypatch):
        _patch_keyring(monkeypatch, _FilledKeyring({"hf_token": "hf_abc"}))
        result = read_secret("hf_token")
        assert result.found is True
        assert result.value == "hf_abc"
        assert result.missing is False
        assert result.backend_error is False

    def test_missing_when_no_entry(self, monkeypatch):
        _patch_keyring(monkeypatch, _EmptyKeyring())
        result = read_secret("hf_token")
        assert result.found is False
        assert result.missing is True
        assert result.backend_error is False
        assert result.value == ""

    def test_backend_error_on_exception(self, monkeypatch):
        _patch_keyring(monkeypatch, _RaisingKeyring())
        result = read_secret("hf_token")
        assert result.found is False
        assert result.missing is False
        assert result.backend_error is True
        assert result.value == ""

    def test_missing_when_no_keyring_installed(self, monkeypatch):
        import core.secrets_store as ss_module
        monkeypatch.setattr(ss_module, "_keyring_module", lambda: None)
        result = read_secret("hf_token")
        assert result.missing is True
        assert result.backend_error is False


class TestSecretReadResultIsImmutable:
    def test_frozen(self):
        r = SecretReadResult(found=True, value="x")
        with pytest.raises((AttributeError, TypeError)):
            r.value = "y"  # type: ignore[misc]
