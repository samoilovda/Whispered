"""
Whispered - YouTube OAuth 2.0 (installed-app flow)

Loopback redirect + PKCE (RFC 8252 / RFC 7636), implemented on ``requests``
and the standard library only — no Google client libraries. The user brings
their own Google Cloud OAuth client (type "Desktop app"); the refresh token
lives in the OS keyring via ``core.secrets_store`` and, when no keyring is
usable, in process memory only (never in ``config.json``).

Network is used only when the user explicitly connects an account or
uploads a video (see CLAUDE.md rule 1).
"""

from __future__ import annotations

import base64
import hashlib
import html
import http.server
import json
import secrets
import threading
import time
import urllib.parse
from typing import Any, Callable, Optional

from core import secrets_store
from core.logger import get_logger

logger = get_logger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"

# ``youtube.upload`` covers videos.insert and thumbnails.set;
# ``youtube.readonly`` is only for showing the channel's name;
# ``youtube.force-ssl`` is the narrowest scope videos.update accepts — for
# fixing an uploaded video's title, description and tags
# (tools/youtube_autoupload.py --update). A login made before it was
# added gets "insufficientPermissions" until the account is reconnected.
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
)

REFRESH_TOKEN_SECRET = "youtube_refresh_token"

LOGIN_TIMEOUT_SECONDS = 300.0
_HTTP_TIMEOUT = (10, 60)
_POLL_INTERVAL = 0.5
_EXPIRY_MARGIN = 60.0


class YouTubeAuthError(Exception):
    """Any failure to obtain or refresh a token. The message is safe to
    show to the user and never contains a token or client secret."""


class YouTubeAuthExpired(YouTubeAuthError):
    """The refresh token was revoked or has expired (``invalid_grant``):
    the user has to connect the account again."""


class YouTubeAuthCancelled(YouTubeAuthError):
    """The user cancelled, or the browser step timed out."""


# ----------------------------------------------------------------- helpers

def generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` using the S256 method."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    query = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    })
    return f"{AUTH_URL}?{query}"


def parse_client_secret_json(text: str) -> tuple[str, str]:
    """Extract ``(client_id, client_secret)`` from a downloaded
    ``client_secret_*.json`` (``installed`` or ``web`` section)."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("not a JSON file") from exc
    section: Any = data.get("installed") or data.get("web") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        raise ValueError("no 'installed' or 'web' section")
    client_id = str(section.get("client_id") or "").strip()
    client_secret = str(section.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise ValueError("client_id/client_secret missing")
    return client_id, client_secret


def _default_session() -> Any:
    import requests
    return requests.Session()


def _error_code(response: Any) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        return str(body.get("error") or "")
    return ""


# ------------------------------------------------------- refresh-token store

_memory_lock = threading.Lock()
_memory_refresh_token: Optional[str] = None


def store_refresh_token(token: str) -> bool:
    """Keep *token* in the OS keyring; fall back to process memory when no
    keyring is usable. Returns True when it was persisted in the keyring."""
    global _memory_refresh_token
    persisted = secrets_store.set_secret(REFRESH_TOKEN_SECRET, token)
    with _memory_lock:
        _memory_refresh_token = None if persisted else token
    if not persisted:
        logger.warning("No usable keyring: the YouTube login lasts until the app quits")
    return persisted


def load_refresh_token() -> Optional[str]:
    with _memory_lock:
        if _memory_refresh_token:
            return _memory_refresh_token
    return secrets_store.get_secret(REFRESH_TOKEN_SECRET) or None


def clear_refresh_token() -> None:
    global _memory_refresh_token
    with _memory_lock:
        _memory_refresh_token = None
    secrets_store.delete_secret(REFRESH_TOKEN_SECRET)


def is_connected() -> bool:
    return load_refresh_token() is not None


# ------------------------------------------------------------ loopback flow

_PAGE = (
    "<!doctype html><meta charset=utf-8><title>Whispered</title>"
    "<body style=\"font-family:-apple-system,sans-serif;text-align:center;margin-top:20vh\">"
    "<h2>{heading}</h2><p>{body}</p></body>"
)


class _CallbackServer(http.server.HTTPServer):
    """One-shot loopback HTTP server; ``result`` holds the first redirect
    that carries ``code`` or ``error``."""

    def __init__(self, expected_state: str) -> None:
        super().__init__(("127.0.0.1", 0), _CallbackHandler)
        self.expected_state = expected_state
        self.result: Optional[dict[str, str]] = None
        self.timeout = _POLL_INTERVAL

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        params = {
            key: values[0]
            for key, values in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()
        }
        if "code" not in params and "error" not in params:
            self.send_error(404)          # favicon and other noise
            return
        if params.get("state") != self.server.expected_state:
            params = {"error": "state_mismatch"}
        if self.server.result is None:
            self.server.result = params
        ok = "code" in params and "error" not in params
        page = _PAGE.format(
            heading=html.escape("Whispered — connected" if ok else "Whispered — not connected"),
            body=html.escape("You can close this tab." if ok else "Return to Whispered and try again."),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - base signature
        pass                              # the redirect URL carries the auth code


def authorize(
    client_id: str,
    client_secret: str,
    *,
    open_browser: Callable[[str], object],
    is_cancelled: Callable[[], bool] = lambda: False,
    session: Any = None,
    timeout: float = LOGIN_TIMEOUT_SECONDS,
) -> str:
    """Run the browser consent flow and return the refresh token (already
    saved via :func:`store_refresh_token`). Polls so that ``is_cancelled()``
    and *timeout* take effect while waiting for the redirect."""
    if not client_id or not client_secret:
        raise YouTubeAuthError("OAuth client is not configured")
    session = session or _default_session()
    verifier, challenge = generate_pkce()
    state = secrets.token_urlsafe(24)
    server = _CallbackServer(state)
    try:
        open_browser(build_auth_url(client_id, server.redirect_uri, state, challenge))
        deadline = time.monotonic() + timeout
        while server.result is None:
            if is_cancelled():
                raise YouTubeAuthCancelled("cancelled")
            if time.monotonic() >= deadline:
                raise YouTubeAuthCancelled("timed out")
            server.handle_request()
        result = server.result
        redirect_uri = server.redirect_uri
    finally:
        server.server_close()

    if "error" in result:
        if result["error"] == "access_denied":
            raise YouTubeAuthCancelled("access denied")
        raise YouTubeAuthError(f"Google returned an error: {result['error']}")

    try:
        response = session.post(TOKEN_URL, data={
            "code": result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        }, timeout=_HTTP_TIMEOUT)
    except Exception as exc:
        raise YouTubeAuthError(f"Could not reach Google: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise YouTubeAuthError(
            f"Token exchange failed ({response.status_code} {_error_code(response)})".strip())
    body = response.json()
    refresh_token = body.get("refresh_token")
    if not refresh_token:
        raise YouTubeAuthError("Google did not return a refresh token; try connecting again")
    store_refresh_token(refresh_token)
    return str(refresh_token)


# ------------------------------------------------------------- token provider

class TokenProvider:
    """Hands out access tokens, refreshing them from the stored refresh
    token shortly before they expire. Thread-safe."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        session: Any = None,
        clock: Callable[[], float] = time.monotonic,
        refresh_token: Optional[Callable[[], Optional[str]]] = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._session = session or _default_session()
        self._clock = clock
        self._refresh_token = refresh_token or load_refresh_token
        self._lock = threading.Lock()
        self._access_token: Optional[str] = None
        self._expires_at = 0.0

    def invalidate(self) -> None:
        """Drop the cached access token (e.g. after a 401)."""
        with self._lock:
            self._access_token = None

    def access_token(self) -> str:
        with self._lock:
            if self._access_token and self._clock() < self._expires_at:
                return self._access_token
            token = self._refresh_token()
            if not token:
                raise YouTubeAuthExpired("not connected")
            try:
                response = self._session.post(TOKEN_URL, data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": token,
                    "grant_type": "refresh_token",
                }, timeout=_HTTP_TIMEOUT)
            except Exception as exc:
                raise YouTubeAuthError(f"Could not reach Google: {type(exc).__name__}") from exc
            if response.status_code != 200:
                code = _error_code(response)
                if code == "invalid_grant":
                    raise YouTubeAuthExpired("the login was revoked or expired")
                raise YouTubeAuthError(f"Token refresh failed ({response.status_code} {code})".strip())
            body = response.json()
            self._access_token = str(body["access_token"])
            self._expires_at = self._clock() + float(body.get("expires_in", 3600)) - _EXPIRY_MARGIN
            return self._access_token


def fetch_channel_title(access_token: str, *, session: Any = None) -> str:
    """Name of the signed-in user's channel, or "" when it can't be read."""
    session = session or _default_session()
    try:
        response = session.get(
            CHANNELS_URL, params={"part": "snippet", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"}, timeout=_HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            return ""
        items = response.json().get("items") or []
        return str(items[0]["snippet"]["title"]) if items else ""
    except Exception as exc:
        logger.warning("Could not read the YouTube channel name: %s", type(exc).__name__)
        return ""


def disconnect(*, session: Any = None) -> None:
    """Revoke the refresh token at Google (best effort) and forget it."""
    token = load_refresh_token()
    clear_refresh_token()
    if not token:
        return
    try:
        (session or _default_session()).post(
            REVOKE_URL, data={"token": token}, timeout=_HTTP_TIMEOUT)
    except Exception as exc:
        logger.warning("Could not revoke the YouTube token: %s", type(exc).__name__)
