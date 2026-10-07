import hashlib
import base64
import json
import threading
import urllib.parse
import urllib.request

import pytest

from core import youtube_oauth as oauth


class _Response:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {}

    def json(self):
        return self._body


class _Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, data=None, timeout=None, **kw):
        self.calls.append(("post", url, dict(data or {})))
        return self.responses.pop(0)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("get", url, dict(params or {}), dict(headers or {})))
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def _memory_store(monkeypatch):
    """Keep the refresh token out of the real keyring."""
    store = {}
    monkeypatch.setattr(oauth.secrets_store, "set_secret", lambda n, v: store.update({n: v}) or True)
    monkeypatch.setattr(oauth.secrets_store, "get_secret", lambda n: store.get(n))
    monkeypatch.setattr(oauth.secrets_store, "delete_secret", lambda n: store.pop(n, None))
    monkeypatch.setattr(oauth, "_memory_refresh_token", None)
    return store


def _params(url):
    return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}


def _browser_that_redirects(**override):
    """open_browser stand-in: hits the loopback redirect like the real
    browser would after consent."""
    def open_browser(url):
        params = _params(url)
        query = {"code": "auth-code", "state": params["state"], **override}
        target = params["redirect_uri"] + "/?" + urllib.parse.urlencode(query)
        threading.Thread(target=lambda: urllib.request.urlopen(target, timeout=5).read()).start()
    return open_browser


def test_pkce_challenge_is_s256_of_the_verifier():
    verifier, challenge = oauth.generate_pkce()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and 43 <= len(verifier) <= 128


def test_auth_url_carries_pkce_state_scopes_and_offline_access():
    url = oauth.build_auth_url("cid", "http://127.0.0.1:9", "st", "chal")
    params = _params(url)
    assert url.startswith(oauth.AUTH_URL)
    assert params["code_challenge"] == "chal" and params["code_challenge_method"] == "S256"
    assert params["state"] == "st" and params["response_type"] == "code"
    assert params["access_type"] == "offline" and params["prompt"] == "consent"
    assert params["scope"].split() == list(oauth.SCOPES)


def test_parse_client_secret_json():
    text = json.dumps({"installed": {"client_id": "a", "client_secret": "b"}})
    assert oauth.parse_client_secret_json(text) == ("a", "b")
    assert oauth.parse_client_secret_json(json.dumps({"web": {"client_id": "a", "client_secret": "b"}})) == ("a", "b")
    for bad in ("nope", "{}", json.dumps({"installed": {"client_id": "a"}})):
        with pytest.raises(ValueError):
            oauth.parse_client_secret_json(bad)


def test_authorize_exchanges_the_code_and_stores_the_refresh_token(_memory_store):
    session = _Session(_Response(200, {"access_token": "at", "refresh_token": "rt"}))
    token = oauth.authorize("cid", "csec", open_browser=_browser_that_redirects(), session=session)
    assert token == "rt"
    assert _memory_store[oauth.REFRESH_TOKEN_SECRET] == "rt"
    _, url, data = session.calls[0]
    assert url == oauth.TOKEN_URL
    assert data["grant_type"] == "authorization_code" and data["code"] == "auth-code"
    assert data["client_id"] == "cid" and data["client_secret"] == "csec"
    assert data["code_verifier"] and data["redirect_uri"].startswith("http://127.0.0.1:")


def test_state_mismatch_is_rejected_without_a_token_request():
    session = _Session()
    with pytest.raises(oauth.YouTubeAuthError, match="state_mismatch"):
        oauth.authorize("cid", "csec", open_browser=_browser_that_redirects(state="forged"), session=session)
    assert session.calls == []


def test_access_denied_is_a_cancellation():
    def deny(url):
        params = _params(url)
        target = params["redirect_uri"] + "/?" + urllib.parse.urlencode(
            {"error": "access_denied", "state": params["state"]})
        threading.Thread(target=lambda: urllib.request.urlopen(target, timeout=5).read()).start()

    with pytest.raises(oauth.YouTubeAuthCancelled):
        oauth.authorize("cid", "csec", open_browser=deny, session=_Session())


def test_cancel_while_waiting_for_the_redirect():
    calls = {"n": 0}

    def cancelled():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(oauth.YouTubeAuthCancelled):
        oauth.authorize("cid", "csec", open_browser=lambda url: None,
                        is_cancelled=cancelled, session=_Session())


def test_timeout_while_waiting_for_the_redirect():
    with pytest.raises(oauth.YouTubeAuthCancelled, match="timed out"):
        oauth.authorize("cid", "csec", open_browser=lambda url: None,
                        session=_Session(), timeout=0.0)


def test_missing_refresh_token_in_the_response_is_an_error():
    session = _Session(_Response(200, {"access_token": "at"}))
    with pytest.raises(oauth.YouTubeAuthError, match="refresh token"):
        oauth.authorize("cid", "csec", open_browser=_browser_that_redirects(), session=session)


def test_failed_exchange_never_leaks_secrets_in_the_message():
    session = _Session(_Response(400, {"error": "invalid_client"}))
    with pytest.raises(oauth.YouTubeAuthError) as info:
        oauth.authorize("cid", "csec-secret", open_browser=_browser_that_redirects(), session=session)
    assert "csec-secret" not in str(info.value) and "invalid_client" in str(info.value)


def test_token_provider_caches_until_near_expiry_then_refreshes():
    now = [0.0]
    session = _Session(
        _Response(200, {"access_token": "a1", "expires_in": 3600}),
        _Response(200, {"access_token": "a2", "expires_in": 3600}),
    )
    provider = oauth.TokenProvider("cid", "csec", session=session,
                                   clock=lambda: now[0], refresh_token=lambda: "rt")
    assert provider.access_token() == "a1"
    now[0] = 3000.0
    assert provider.access_token() == "a1" and len(session.calls) == 1
    now[0] = 3550.0                    # inside the 60 s margin
    assert provider.access_token() == "a2"
    assert session.calls[0][2]["grant_type"] == "refresh_token"
    assert session.calls[0][2]["refresh_token"] == "rt"


def test_invalid_grant_means_the_login_expired():
    provider = oauth.TokenProvider("cid", "csec", refresh_token=lambda: "rt",
                                   session=_Session(_Response(400, {"error": "invalid_grant"})))
    with pytest.raises(oauth.YouTubeAuthExpired):
        provider.access_token()


def test_not_connected_is_reported_as_expired():
    provider = oauth.TokenProvider("cid", "csec", session=_Session(), refresh_token=lambda: None)
    with pytest.raises(oauth.YouTubeAuthExpired):
        provider.access_token()


def test_invalidate_forces_a_refresh():
    session = _Session(_Response(200, {"access_token": "a1"}), _Response(200, {"access_token": "a2"}))
    provider = oauth.TokenProvider("cid", "csec", session=session, refresh_token=lambda: "rt")
    assert provider.access_token() == "a1"
    provider.invalidate()
    assert provider.access_token() == "a2"


def test_refresh_token_falls_back_to_memory_without_a_keyring(monkeypatch):
    monkeypatch.setattr(oauth.secrets_store, "set_secret", lambda n, v: False)
    monkeypatch.setattr(oauth.secrets_store, "get_secret", lambda n: None)
    assert oauth.store_refresh_token("rt") is False
    assert oauth.load_refresh_token() == "rt" and oauth.is_connected()
    oauth.clear_refresh_token()
    assert not oauth.is_connected()


def test_channel_title_and_failures():
    ok = _Session(_Response(200, {"items": [{"snippet": {"title": "My channel"}}]}))
    assert oauth.fetch_channel_title("at", session=ok) == "My channel"
    assert ok.calls[0][3]["Authorization"] == "Bearer at"
    assert oauth.fetch_channel_title("at", session=_Session(_Response(403))) == ""
    assert oauth.fetch_channel_title("at", session=_Session(_Response(200, {"items": []}))) == ""


def test_disconnect_revokes_and_forgets(_memory_store):
    oauth.store_refresh_token("rt")
    session = _Session(_Response(200))
    oauth.disconnect(session=session)
    assert session.calls[0][1] == oauth.REVOKE_URL and session.calls[0][2] == {"token": "rt"}
    assert not oauth.is_connected() and oauth.REFRESH_TOKEN_SECRET not in _memory_store
