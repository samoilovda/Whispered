import json
from pathlib import Path

import pytest

from core import youtube_upload as up
from domain.youtube_publish import PublishPackage


class _Response:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._body


def _error(status, reason):
    return _Response(status, {"error": {"errors": [{"reason": reason}]}})


class _Tokens:
    def __init__(self):
        self.invalidated = 0
        self.n = 0

    def access_token(self):
        self.n += 1
        return f"tok{self.n}"

    def invalidate(self):
        self.invalidated += 1


class _Session:
    """Scripted server: ``handler(method, url, headers, data, params)``
    returns a response or raises."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def _call(self, method, url, headers=None, data=None, params=None, timeout=None):
        self.calls.append((method, url, dict(headers or {}), data, params))
        return self.handler(method, url, headers or {}, data, params)

    def post(self, url, **kw):
        return self._call("post", url, **kw)

    def put(self, url, **kw):
        return self._call("put", url, **kw)


SESSION_URI = "https://upload.example/session/1"


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "talk.mp4"
    path.write_bytes(bytes(range(256)) * 40)          # 10240 bytes
    return path


def _pkg(video, **kw):
    args = dict(video_path=video, title="T", description="D", tags=("a", "b"),
                thumbnail_path=None, language="ru")
    args.update(kw)
    return PublishPackage(**args)


def _client(session, sleeps=None, chunk=4096, tokens=None):
    sleeps = sleeps if sleeps is not None else []
    return up.UploadClient(tokens or _Tokens(), session=session, sleep=sleeps.append, chunk_size=chunk), sleeps


def _paths(tmp_path):
    return tmp_path / "pending.json", tmp_path / "record.json"


def _content_range(headers):
    spec = headers["Content-Range"]                    # "bytes a-b/total" or "bytes */total"
    return spec


class _Server:
    """A well-behaved resumable endpoint recording what it received."""

    def __init__(self, total):
        self.total = total
        self.received = 0
        self.fail_next = []                            # scripted responses/exceptions for chunk PUTs

    def __call__(self, method, url, headers, data, params):
        if method == "post" and url == up.UPLOAD_URL:
            return _Response(200, headers={"Location": SESSION_URI})
        if method == "post" and url == up.THUMBNAIL_URL:
            return _Response(200, {})
        assert method == "put" and url == SESSION_URI
        spec = _content_range(headers)
        if spec.startswith("bytes */"):
            if self.received >= self.total:
                return _Response(200, {"id": "vid123"})
            hdr = {"Range": f"bytes=0-{self.received - 1}"} if self.received else {}
            return _Response(308, headers=hdr)
        if self.fail_next:
            outcome = self.fail_next.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        first, rest = spec[len("bytes "):].split("-")
        last = int(rest.split("/")[0])
        assert int(first) == self.received, "client skipped or repeated bytes"
        self.received = last + 1
        if self.received >= self.total:
            return _Response(200, {"id": "vid123"})
        return _Response(308, headers={"Range": f"bytes=0-{last}"})


def test_metadata_has_snippet_status_and_language():
    meta = up.build_metadata(_pkg(Path("x.mp4")))
    assert meta["snippet"]["title"] == "T" and meta["snippet"]["tags"] == ["a", "b"]
    assert meta["snippet"]["defaultLanguage"] == "ru" == meta["snippet"]["defaultAudioLanguage"]
    assert meta["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False}
    assert "defaultLanguage" not in up.build_metadata(_pkg(Path("x.mp4"), language="Russian"))["snippet"]


def test_full_upload_over_several_chunks(video, tmp_path):
    server = _Server(video.stat().st_size)
    session = _Session(server)
    client, _ = _client(session)
    progress = []
    result = client.publish(_pkg(video), pending_path=_paths(tmp_path)[0],
                            record_path=_paths(tmp_path)[1], on_progress=lambda a, b: progress.append((a, b)))
    assert result.record.video_id == "vid123" and result.thumbnail_warning is None
    assert server.received == video.stat().st_size
    assert progress[0] == (0, 10240) and progress[-1] == (10240, 10240)
    init = session.calls[0]
    assert init[4] == {"uploadType": "resumable", "part": "snippet,status"}
    assert init[2]["X-Upload-Content-Length"] == "10240"
    assert json.loads(init[3])["snippet"]["title"] == "T"
    assert not _paths(tmp_path)[0].exists()                       # pending cleared
    assert up.load_upload_record(_paths(tmp_path)[1]).video_id == "vid123"


def test_308_with_a_partial_range_resumes_from_what_the_server_has(video, tmp_path):
    sent = []

    def handler(method, url, headers, data, params):
        if method == "post":
            return _Response(200, headers={"Location": SESSION_URI})
        spec = headers["Content-Range"]
        first = int(spec[len("bytes "):].split("-")[0])
        sent.append(first)
        if first == 0:
            return _Response(308, headers={"Range": "bytes=0-1023"})     # only 1 KiB of 4 KiB kept
        return _Response(200, {"id": "v"})

    client, _ = _client(_Session(handler))
    assert client.upload_video(_pkg(video), pending_path=_paths(tmp_path)[0]) == "v"
    assert sent[:2] == [0, 1024]


def test_server_error_backs_off_then_resumes_from_the_confirmed_byte(video, tmp_path):
    server = _Server(video.stat().st_size)
    server.fail_next = [_Response(503)]
    client, sleeps = _client(_Session(server))
    assert client.upload_video(_pkg(video), pending_path=_paths(tmp_path)[0]) == "vid123"
    assert sum(sleeps) == 1 and server.received == video.stat().st_size


def test_network_error_is_retried_with_growing_backoff(video, tmp_path):
    server = _Server(video.stat().st_size)
    server.fail_next = [ConnectionError("x"), ConnectionError("x"), ConnectionError("x")]
    client, sleeps = _client(_Session(server))
    assert client.upload_video(_pkg(video), pending_path=_paths(tmp_path)[0]) == "vid123"
    assert sum(sleeps) == 1 + 2 + 4


def test_gives_up_after_the_retry_budget(video, tmp_path):
    server = _Server(video.stat().st_size)
    server.fail_next = [_Response(503)] * 20
    client, sleeps = _client(_Session(server))
    with pytest.raises(up.YouTubeUploadError, match="kept failing"):
        client.upload_video(_pkg(video), pending_path=_paths(tmp_path)[0])
    assert max(sleeps) <= 0.5 and sum(sleeps) <= sum(min(2 ** i, 64) for i in range(8)) + 1e-6


def test_401_refreshes_the_token_once_and_retries(video, tmp_path):
    calls = {"n": 0}
    tokens = _Tokens()

    def handler(method, url, headers, data, params):
        calls["n"] += 1
        if calls["n"] == 1:
            assert headers["Authorization"] == "Bearer tok1"
            return _Response(401)
        if method == "post":
            assert headers["Authorization"] == "Bearer tok2"
            return _Response(200, headers={"Location": SESSION_URI})
        return _Response(200, {"id": "v"})

    client, _ = _client(_Session(handler), tokens=tokens, chunk=1 << 20)
    assert client.upload_video(_pkg(video), pending_path=_paths(tmp_path)[0]) == "v"
    assert tokens.invalidated == 1


@pytest.mark.parametrize("reason, exc", [
    ("quotaExceeded", up.QuotaExceeded),
    ("uploadLimitExceeded", up.UploadForbidden),
    ("forbidden", up.UploadForbidden),
])
def test_403_reasons_become_typed_errors_without_retries(video, tmp_path, reason, exc):
    session = _Session(lambda *a: _error(403, reason))
    client, sleeps = _client(session)
    with pytest.raises(exc):
        client.upload_video(_pkg(video), pending_path=_paths(tmp_path)[0])
    assert sleeps == [] and len(session.calls) == 1


def test_cancel_between_chunks_keeps_the_pending_session(video, tmp_path):
    server = _Server(video.stat().st_size)
    client, _ = _client(_Session(server))
    seen = {"n": 0}

    def cancelled():
        seen["n"] += 1
        return seen["n"] > 2                          # let one chunk through

    pending, _rec = _paths(tmp_path)
    with pytest.raises(up.UploadCancelled):
        client.upload_video(_pkg(video), pending_path=pending, is_cancelled=cancelled)
    assert 0 < server.received < video.stat().st_size
    assert up.load_pending(pending, video) == SESSION_URI


def test_interrupted_upload_continues_from_the_pending_session(video, tmp_path):
    server = _Server(video.stat().st_size)
    pending, _rec = _paths(tmp_path)
    up.save_pending(pending, video, SESSION_URI, "T")
    server.received = 4096                            # what the first run got through
    session = _Session(server)
    client, _ = _client(session)
    assert client.upload_video(_pkg(video), pending_path=pending) == "vid123"
    assert not any(c[1] == up.UPLOAD_URL for c in session.calls)      # no new session
    assert server.received == video.stat().st_size


def test_expired_pending_session_starts_over(video, tmp_path):
    state = {"expired": True}
    server = _Server(video.stat().st_size)

    def handler(method, url, headers, data, params):
        if method == "put" and url == "https://upload.example/old":
            return _error(404, "notFound")
        return server(method, url, headers, data, params)

    pending, _rec = _paths(tmp_path)
    up.save_pending(pending, video, "https://upload.example/old", "T")
    client, _ = _client(_Session(handler))
    assert client.upload_video(_pkg(video), pending_path=pending) == "vid123"
    assert state and up.load_pending(pending, video) == SESSION_URI


def test_pending_for_a_changed_file_is_ignored(video, tmp_path):
    pending, _rec = _paths(tmp_path)
    up.save_pending(pending, video, SESSION_URI, "T")
    video.write_bytes(b"different size")
    assert up.load_pending(pending, video) is None


def test_session_already_complete_returns_without_resending(video, tmp_path):
    server = _Server(video.stat().st_size)
    server.received = video.stat().st_size
    pending, _rec = _paths(tmp_path)
    up.save_pending(pending, video, SESSION_URI, "T")
    session = _Session(server)
    client, _ = _client(session)
    assert client.upload_video(_pkg(video), pending_path=pending) == "vid123"
    assert len(session.calls) == 1


def test_thumbnail_is_uploaded_with_the_right_content_type(video, tmp_path):
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png-bytes")
    session = _Session(_Server(video.stat().st_size))
    client, _ = _client(session)
    result = client.publish(_pkg(video, thumbnail_path=cover), pending_path=_paths(tmp_path)[0],
                            record_path=_paths(tmp_path)[1])
    assert result.thumbnail_warning is None
    method, url, headers, data, params = session.calls[-1]
    assert url == up.THUMBNAIL_URL and headers["Content-Type"] == "image/png"
    assert data == b"png-bytes" and params == {"videoId": "vid123", "uploadType": "media"}


def test_a_failing_cover_is_only_a_warning(video, tmp_path):
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"png")
    server = _Server(video.stat().st_size)

    def handler(method, url, headers, data, params):
        if url == up.THUMBNAIL_URL:
            return _error(403, "forbidden")
        return server(method, url, headers, data, params)

    client, _ = _client(_Session(handler))
    result = client.publish(_pkg(video, thumbnail_path=cover), pending_path=_paths(tmp_path)[0],
                            record_path=_paths(tmp_path)[1])
    assert result.record.video_id == "vid123"
    assert "phone" in (result.thumbnail_warning or "")
    assert up.load_upload_record(_paths(tmp_path)[1]) is not None


def test_unsupported_cover_type_is_a_warning_not_a_crash(video, tmp_path):
    cover = tmp_path / "cover.bmp"
    cover.write_bytes(b"x")
    client, _ = _client(_Session(_Server(video.stat().st_size)))
    result = client.publish(_pkg(video, thumbnail_path=cover), pending_path=_paths(tmp_path)[0],
                            record_path=_paths(tmp_path)[1])
    assert "PNG or JPEG" in (result.thumbnail_warning or "")


def test_empty_video_is_rejected(tmp_path):
    empty = tmp_path / "e.mp4"
    empty.write_bytes(b"")
    client, _ = _client(_Session(lambda *a: _Response()))
    with pytest.raises(up.YouTubeUploadError, match="empty"):
        client.upload_video(_pkg(empty), pending_path=tmp_path / "p.json")
