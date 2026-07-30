"""Unit tests for core/secrets_store.py.

Every test monkeypatches _keyring_module() rather than touching the real
`keyring` package — this module is reachable to a real OS backend
(Keychain/Credential Manager) from a plain test run, and these tests
must never write to it.
"""

import sys

from core import secrets_store


class TestKeyringModuleImport:
    def test_returns_none_when_the_package_is_not_installed(self, monkeypatch):
        # A None entry in sys.modules makes `import keyring` raise
        # ImportError, the same as it never having been installed.
        monkeypatch.setitem(sys.modules, "keyring", None)
        assert secrets_store._keyring_module() is None

    def test_returns_the_module_when_installed(self, monkeypatch):
        fake_module = object()
        monkeypatch.setitem(sys.modules, "keyring", fake_module)
        assert secrets_store._keyring_module() is fake_module


class _FakeBackend:
    """Stand-in for the `keyring` module's public surface."""

    def __init__(self, raise_on=()):
        self._store: dict[str, str] = {}
        self._raise_on = set(raise_on)

    def set_password(self, service, name, value):
        if "set" in self._raise_on:
            raise RuntimeError("backend unavailable")
        self._store[name] = value

    def get_password(self, service, name):
        if "get" in self._raise_on:
            raise RuntimeError("backend unavailable")
        return self._store.get(name)

    def delete_password(self, service, name):
        if "delete" in self._raise_on:
            raise RuntimeError("backend unavailable")
        self._store.pop(name, None)


class TestKeyringUnavailable:
    """_keyring_module() returning None (not installed, or no usable
    backend) — every public function must degrade quietly."""

    def _make_unavailable(self, monkeypatch):
        monkeypatch.setattr(secrets_store, "_keyring_module", lambda: None)

    def test_set_secret_returns_false(self, monkeypatch):
        self._make_unavailable(monkeypatch)
        assert secrets_store.set_secret("hf_token", "value") is False

    def test_get_secret_returns_none(self, monkeypatch):
        self._make_unavailable(monkeypatch)
        assert secrets_store.get_secret("hf_token") is None

    def test_delete_secret_does_not_raise(self, monkeypatch):
        self._make_unavailable(monkeypatch)
        secrets_store.delete_secret("hf_token")  # must not raise


class TestKeyringAvailable:
    def test_set_then_get_round_trips(self, monkeypatch):
        backend = _FakeBackend()
        monkeypatch.setattr(secrets_store, "_keyring_module", lambda: backend)

        assert secrets_store.set_secret("hf_token", "hf_secret") is True
        assert secrets_store.get_secret("hf_token") == "hf_secret"

    def test_uses_a_single_service_name_for_all_secrets(self, monkeypatch):
        backend = _FakeBackend()
        monkeypatch.setattr(secrets_store, "_keyring_module", lambda: backend)

        calls = []
        original = backend.set_password
        backend.set_password = lambda service, name, value: (
            calls.append(service), original(service, name, value)
        )
        secrets_store.set_secret("a", "1")
        secrets_store.set_secret("b", "2")

        assert calls == [secrets_store._SERVICE_NAME, secrets_store._SERVICE_NAME]

    def test_get_missing_key_returns_none(self, monkeypatch):
        backend = _FakeBackend()
        monkeypatch.setattr(secrets_store, "_keyring_module", lambda: backend)
        assert secrets_store.get_secret("never_set") is None

    def test_delete_removes_the_entry(self, monkeypatch):
        backend = _FakeBackend()
        monkeypatch.setattr(secrets_store, "_keyring_module", lambda: backend)
        secrets_store.set_secret("hf_token", "hf_secret")

        secrets_store.delete_secret("hf_token")

        assert secrets_store.get_secret("hf_token") is None

    def test_set_secret_backend_error_falls_back_to_false(self, monkeypatch):
        backend = _FakeBackend(raise_on={"set"})
        monkeypatch.setattr(secrets_store, "_keyring_module", lambda: backend)

        assert secrets_store.set_secret("hf_token", "value") is False

    def test_get_secret_backend_error_falls_back_to_none(self, monkeypatch):
        backend = _FakeBackend(raise_on={"get"})
        monkeypatch.setattr(secrets_store, "_keyring_module", lambda: backend)

        assert secrets_store.get_secret("hf_token") is None

    def test_delete_secret_backend_error_does_not_raise(self, monkeypatch):
        backend = _FakeBackend(raise_on={"delete"})
        monkeypatch.setattr(secrets_store, "_keyring_module", lambda: backend)

        secrets_store.delete_secret("hf_token")  # must not raise


class TestSentinel:
    def test_sentinel_is_not_empty_string(self):
        # Empty string means "not configured"; the sentinel must be
        # distinguishable from it so Config.load() doesn't confuse the two.
        assert secrets_store.KEYRING_SENTINEL != ""
