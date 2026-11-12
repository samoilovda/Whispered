"""
Whispered - OS keyring-backed secret storage
Optional layer over Config's plaintext secret fields (hf_token,
yt_openai_api_key, yt_anthropic_api_key): when the `keyring` package is
installed and a working OS backend is available (macOS Keychain,
Windows Credential Manager, a Secret Service on Linux), secrets are
stored there instead of in config.json. Falls back to the previous,
still fully supported plaintext-file behavior whenever keyring is
unavailable or fails — this is a soft dependency, never a hard
requirement to run the app (see requirements.txt).

On macOS the backend is Apple's ``/usr/bin/security`` tool rather than the
``keyring`` package, so an app update does not lose Keychain access (see
``_SecurityCliKeyring``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

from core.logger import get_logger

logger = get_logger(__name__)

_SERVICE_NAME = "Whispered"

# What config.json stores in place of a secret that has been moved into
# the OS keyring. Distinct from "" (which means "not configured") so
# Config.load() knows to look the real value up instead of treating the
# field as empty.
KEYRING_SENTINEL = "__keyring__"


@dataclass(frozen=True)
class SecretReadResult:
    """Tri-state result of a keyring lookup.

    Attributes:
        found:         True when the secret was successfully read.
        value:         The secret value; non-empty only when ``found`` is True.
        missing:       True when the keyring is reachable but has no entry.
        backend_error: True when the keyring raised an exception (broken
                       backend, locked keychain, etc.).  The caller should
                       preserve the KEYRING_SENTINEL rather than treating
                       the secret as empty.
    """

    found: bool = False
    value: str = ""
    missing: bool = False
    backend_error: bool = False


def _legacy_keyring():
    """Import the ``keyring`` package lazily; None when it is not installed."""
    try:
        import keyring
        return keyring
    except ImportError:
        return None


# ------------------------------------------------------- macOS: security CLI
#
# A Keychain item written by the app itself is locked to that build: its
# partition list holds "cdhash:<hash of this binary>" (a self-signed or
# ad-hoc app has no Team ID to put there instead), so every rebuild or
# update made macOS ask for the login password again. Items written through
# Apple's own /usr/bin/security get the stable "apple-tool:" partition and
# trust that tool, which never changes between our builds — no prompts.
# Trade-off: any process of this user can read them through the same tool,
# much like a file in the user's home folder.

_SECURITY_TOOL = "/usr/bin/security"
# Its own service name, so an entry written by an older build through the
# ``keyring`` package (service "Whispered") is recognised and moved once.
_CLI_SERVICE = "io.github.whispered"
_CLI_NOT_FOUND = 44
_CLI_TIMEOUT = 120.0          # a locked Keychain shows an unlock dialog
# tests/ and tests_qt/ set this to "keyring" so they never reach the real
# Keychain through the CLI (their conftest swaps in an in-memory keyring).
BACKEND_ENV = "WHISPERED_KEYRING_BACKEND"


class SecurityCliError(Exception):
    """``/usr/bin/security`` failed for a reason other than "not found"."""


def _use_security_cli() -> bool:
    return (sys.platform == "darwin" and os.environ.get(BACKEND_ENV) != "keyring"
            and os.path.exists(_SECURITY_TOOL))


def _run_security(args: list[str], stdin: Optional[str] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [_SECURITY_TOOL, *args], input=stdin, capture_output=True, text=True,
            timeout=_CLI_TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SecurityCliError(type(exc).__name__) from exc


class _SecurityCliKeyring:
    """``keyring``-shaped adapter over ``/usr/bin/security`` (macOS only)."""

    def get_password(self, service: str, name: str) -> Optional[str]:
        result = _run_security(["find-generic-password", "-s", _CLI_SERVICE, "-a", name, "-w"])
        if result.returncode == 0:
            return result.stdout.removesuffix("\n")
        if result.returncode != _CLI_NOT_FOUND:
            raise SecurityCliError(f"find-generic-password returned {result.returncode}")
        return self._migrate(service, name)

    def set_password(self, service: str, name: str, value: str) -> None:
        # Interactive mode reads the command from stdin, so the secret never
        # shows up in the process list; hex needs no quoting.
        command = (f"add-generic-password -U -s {_CLI_SERVICE} -a {name} -l Whispered "
                   f"-X {value.encode('utf-8').hex()}\n")
        result = _run_security(["-i"], stdin=command)
        if result.returncode != 0:
            raise SecurityCliError(f"add-generic-password returned {result.returncode}")

    def delete_password(self, service: str, name: str) -> None:
        result = _run_security(["delete-generic-password", "-s", _CLI_SERVICE, "-a", name])
        removed_legacy = self._delete_legacy(service, name)
        if result.returncode == 0 or removed_legacy:
            return
        if result.returncode == _CLI_NOT_FOUND:
            raise KeyError(name)
        raise SecurityCliError(f"delete-generic-password returned {result.returncode}")

    def _migrate(self, service: str, name: str) -> Optional[str]:
        """Move an entry an older build stored through ``keyring`` (macOS may
        ask for the password this one last time) to the CLI's own item."""
        legacy = _legacy_keyring()
        if legacy is None:
            return None
        value = legacy.get_password(service, name)
        if value is None:
            return None
        self.set_password(service, name, value)
        self._delete_legacy(service, name)
        logger.info("Moved %s to a Keychain item that survives app updates", name)
        return value

    @staticmethod
    def _delete_legacy(service: str, name: str) -> bool:
        legacy = _legacy_keyring()
        if legacy is None:
            return False
        try:
            legacy.delete_password(service, name)
            return True
        except Exception:  # noqa: BLE001 - absent or not ours to remove
            return False


def _keyring_module():
    """The secret backend: on macOS the ``security`` CLI adapter, elsewhere
    the ``keyring`` package. None means "not installed" or "no usable
    backend" — either way, callers fall back to plaintext storage."""
    if _use_security_cli():
        return _SecurityCliKeyring()
    return _legacy_keyring()


def set_secret(name: str, value: str) -> bool:
    """Store ``value`` in the OS keyring under ``name``.

    Returns True on success. False means keyring is unavailable (not
    installed, or the platform has no usable backend — e.g. headless
    Linux without a Secret Service) and the caller should keep storing
    the value in config.json as before.
    """
    keyring = _keyring_module()
    if keyring is None:
        return False
    try:
        keyring.set_password(_SERVICE_NAME, name, value)
        return True
    except Exception as exc:
        logger.warning("Keyring write failed for %s, falling back to file: %s", name, exc)
        return False


def read_secret(name: str) -> SecretReadResult:
    """Look ``name`` up in the OS keyring; return a :class:`SecretReadResult`.

    Use this instead of :func:`get_secret` when the caller needs to
    distinguish a missing entry from a broken backend (e.g. to decide
    whether to preserve the KEYRING_SENTINEL or replace it with ``""``).
    """
    keyring = _keyring_module()
    if keyring is None:
        # No keyring installed — treat as missing, not an error.
        return SecretReadResult(missing=True)
    try:
        value = keyring.get_password(_SERVICE_NAME, name)
        if value is None:
            return SecretReadResult(missing=True)
        return SecretReadResult(found=True, value=value)
    except Exception as exc:
        logger.warning("Keyring read failed for %s: %s", name, exc)
        return SecretReadResult(backend_error=True)


def get_secret(name: str) -> Optional[str]:
    """Look ``name`` up in the OS keyring. None if unavailable, not
    found, or the lookup failed."""
    keyring = _keyring_module()
    if keyring is None:
        return None
    try:
        return keyring.get_password(_SERVICE_NAME, name)
    except Exception as exc:
        logger.warning("Keyring read failed for %s: %s", name, exc)
        return None


def delete_secret(name: str) -> None:
    """Remove ``name`` from the OS keyring, if present. Never raises —
    used for best-effort cleanup (e.g. the user clears a token field)."""
    keyring = _keyring_module()
    if keyring is None:
        return
    try:
        keyring.delete_password(_SERVICE_NAME, name)
    except Exception as exc:  # noqa: BLE001 - documented as never raising
        logger.debug("Keyring: nothing removed for %s: %s", name, exc)
