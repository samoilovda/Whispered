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


# ------------------------------------------------- macOS security CLI backend

class _FakeSecurityTool:
    """Stands in for /usr/bin/security: parses the few commands the adapter
    sends and keeps items in a dict keyed by (service, account)."""

    def __init__(self):
        self.items = {}
        self.calls = []

    def __call__(self, args, stdin=None):
        import subprocess

        self.calls.append((list(args), stdin))
        if args == ["-i"]:
            words = stdin.split()
            assert words[0] == "add-generic-password"
            opts = {}
            it = iter(words[1:])
            for word in it:
                if word in ("-s", "-a", "-l", "-X"):
                    opts[word] = next(it)
            self.items[(opts["-s"], opts["-a"])] = bytes.fromhex(opts["-X"]).decode("utf-8")
            return subprocess.CompletedProcess(args, 0, "", "")
        opts = dict(zip(args[1::2], args[2::2]))
        key = (opts["-s"], opts["-a"])
        if args[0] == "find-generic-password":
            if key in self.items:
                return subprocess.CompletedProcess(args, 0, self.items[key] + "\n", "")
            return subprocess.CompletedProcess(args, 44, "", "not found")
        if args[0] == "delete-generic-password":
            if self.items.pop(key, None) is not None:
                return subprocess.CompletedProcess(args, 0, "", "")
            return subprocess.CompletedProcess(args, 44, "", "not found")
        raise AssertionError(args)


@pytest.fixture
def security_cli(monkeypatch):
    import core.secrets_store as ss

    tool = _FakeSecurityTool()
    legacy = _FilledKeyring()
    legacy.deleted = []

    def delete_password(service, name):
        legacy.deleted.append(name)
        if name not in legacy._store:
            raise RuntimeError("not found")
        del legacy._store[name]

    legacy.delete_password = delete_password
    monkeypatch.setattr(ss, "_use_security_cli", lambda: True)
    monkeypatch.setattr(ss, "_run_security", tool)
    monkeypatch.setattr(ss, "_legacy_keyring", lambda: legacy)
    return ss, tool, legacy


class TestSecurityCliBackend:
    def test_round_trip_keeps_the_secret_off_the_command_line(self, security_cli):
        ss, tool, _legacy = security_cli
        assert ss.set_secret("hf_token", 'hf_a b"c') is True
        assert ss.get_secret("hf_token") == 'hf_a b"c'
        argv = " ".join(" ".join(args) for args, _ in tool.calls)
        assert "hf_a" not in argv
        ss.delete_secret("hf_token")
        assert ss.read_secret("hf_token") == SecretReadResult(missing=True)

    def test_entry_from_an_older_build_moves_once(self, security_cli):
        ss, tool, legacy = security_cli
        legacy._store["youtube_refresh_token"] = "1//old"
        assert ss.get_secret("youtube_refresh_token") == "1//old"
        assert tool.items == {(ss._CLI_SERVICE, "youtube_refresh_token"): "1//old"}
        assert "youtube_refresh_token" not in legacy._store
        assert ss.get_secret("youtube_refresh_token") == "1//old"

    def test_tool_failure_is_a_backend_error_not_missing(self, security_cli, monkeypatch):
        import subprocess

        ss, _tool, _legacy = security_cli
        monkeypatch.setattr(ss, "_run_security",
                            lambda args, stdin=None: subprocess.CompletedProcess(args, 51, "", ""))
        assert ss.read_secret("hf_token").backend_error
        assert ss.set_secret("hf_token", "x") is False

    def test_test_suite_never_selects_the_real_tool(self):
        import core.secrets_store as ss

        assert ss._use_security_cli() is False
