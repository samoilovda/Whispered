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
"""

from __future__ import annotations

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


def _keyring_module():
    """Import keyring lazily; None means "not installed" or "no usable
    backend" — either way, callers fall back to plaintext storage."""
    try:
        import keyring
        return keyring
    except ImportError:
        return None


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
    except Exception:
        pass
