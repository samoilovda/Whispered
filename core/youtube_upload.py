"""
Whispered - YouTube resumable upload client

``videos.insert`` (resumable protocol), ``thumbnails.set`` and
``videos.update`` (title, description and tags of an uploaded video) over plain
``requests``; the HTTP session, the clock and ``sleep`` are injected so the
whole protocol is testable without a network. Qt-free.

Network is used only for an upload the user explicitly started from the
publish dialog (see CLAUDE.md rule 1). The upload session URI is persisted
next to the artifacts so an interrupted upload can be continued later.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NoReturn, Optional, Protocol

from core.logger import get_logger
from domain.youtube_publish import PublishPackage, UploadRecord

logger = get_logger(__name__)

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

CHUNK_SIZE = 8 * 1024 * 1024            # multiple of 256 KiB, as the protocol requires
MAX_RETRIES = 8
MAX_BACKOFF_SECONDS = 64
_REQUEST_TIMEOUT = (10, 120)
_CODE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
_THUMBNAIL_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


class YouTubeUploadError(Exception):
    """An upload failure whose message is safe to show to the user."""


class QuotaExceeded(YouTubeUploadError):
    """The API project's daily quota is used up."""


class UploadForbidden(YouTubeUploadError):
    """YouTube refused the upload (channel limit, missing permission, ...)."""


class UploadSessionExpired(YouTubeUploadError):
    """The resumable session no longer exists; start over."""


class UploadCancelled(YouTubeUploadError):
    """``is_cancelled()`` turned true between chunks."""


class ThumbnailError(YouTubeUploadError):
    """The video is up but its cover could not be set."""


class _Retryable(Exception):
    """Network failure or a 5xx answer: back off and try again."""


class TokenSource(Protocol):
    def access_token(self) -> str: ...
    def invalidate(self) -> None: ...


@dataclass(frozen=True)
class UploadResult:
    record: UploadRecord
    thumbnail_warning: Optional[str] = None


# ------------------------------------------------------------- JSON on disk

def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_upload_record(path: Path, record: UploadRecord) -> None:
    _atomic_write_json(path, {
        "video_id": record.video_id, "uploaded_at": record.uploaded_at,
        "title": record.title, "privacy": record.privacy,
    })


def load_upload_record(path: Path) -> Optional[UploadRecord]:
    data = _read_json(path)
    if not data or not data.get("video_id"):
        return None
    return UploadRecord(
        video_id=str(data["video_id"]), uploaded_at=str(data.get("uploaded_at", "")),
        title=str(data.get("title", "")), privacy=str(data.get("privacy", "private")))


def _fingerprint(video: Path) -> dict:
    stat = video.stat()
    return {"path": str(video), "size": stat.st_size, "mtime": int(stat.st_mtime)}


def save_pending(path: Path, video: Path, session_uri: str, title: str) -> None:
    _atomic_write_json(path, {"session_uri": session_uri, "title": title, **_fingerprint(video)})


def load_pending(path: Path, video: Path) -> Optional[str]:
    """Session URI of an interrupted upload of exactly this file, if any."""
    data = _read_json(path)
    if not data or not data.get("session_uri"):
        return None
    try:
        current = _fingerprint(video)
    except OSError:
        return None
    if any(data.get(key) != value for key, value in current.items()):
        return None
    return str(data["session_uri"])


def clear_pending(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


# ------------------------------------------------------------------- helpers

def _error_reason(response: Any) -> str:
    try:
        body = response.json()
        errors = body["error"]["errors"]
        return str(errors[0].get("reason", ""))
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return ""


def _confirmed_offset(response: Any) -> int:
    """Next byte to send according to a 308 answer's ``Range: bytes=0-N``."""
    header = response.headers.get("Range") or response.headers.get("range") or ""
    match = re.match(r"bytes=0-(\d+)", header)
    return int(match.group(1)) + 1 if match else 0


def _language_code(language: Optional[str]) -> Optional[str]:
    return language if language and _CODE_RE.match(language) else None


def build_metadata(pkg: PublishPackage) -> dict:
    snippet: dict[str, Any] = {
        "title": pkg.title,
        "description": pkg.description,
        "tags": list(pkg.tags),
        "categoryId": pkg.category_id,
    }
    code = _language_code(pkg.language)
    if code:
        snippet["defaultLanguage"] = code
        snippet["defaultAudioLanguage"] = code
    return {
        "snippet": snippet,
        "status": {"privacyStatus": pkg.privacy, "selfDeclaredMadeForKids": pkg.made_for_kids},
    }


# --------------------------------------------------------------------- client

class UploadClient:
    """Uploads one :class:`PublishPackage`. Not thread-safe; one per upload."""

    def __init__(
        self,
        tokens: TokenSource,
        *,
        session: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        chunk_size: int = CHUNK_SIZE,
    ) -> None:
        if session is None:
            import requests
            session = requests.Session()
        self._tokens = tokens
        self._session = session
        self._sleep = sleep
        self._chunk_size = chunk_size

    # ----------------------------------------------------------- public API

    def publish(
        self,
        pkg: PublishPackage,
        *,
        pending_path: Path,
        record_path: Path,
        on_progress: Callable[[int, int], None] = lambda sent, total: None,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> UploadResult:
        """Upload the video, then its cover (a cover failure is only a
        warning). Writes ``record_path`` on success and clears the pending
        session file."""
        video_id = self.upload_video(
            pkg, pending_path=pending_path, on_progress=on_progress, is_cancelled=is_cancelled)
        record = UploadRecord(
            video_id=video_id,
            uploaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            title=pkg.title, privacy=pkg.privacy,
        )
        save_upload_record(record_path, record)
        clear_pending(pending_path)
        warning: Optional[str] = None
        if pkg.thumbnail_path is not None:
            try:
                self.set_thumbnail(video_id, pkg.thumbnail_path)
            except ThumbnailError as exc:
                logger.warning("Cover was not set for %s: %s", video_id, exc)
                warning = str(exc)
        return UploadResult(record, warning)

    def upload_video(
        self,
        pkg: PublishPackage,
        *,
        pending_path: Path,
        on_progress: Callable[[int, int], None] = lambda sent, total: None,
        is_cancelled: Callable[[], bool] = lambda: False,
    ) -> str:
        size = pkg.video_path.stat().st_size
        if size <= 0:
            raise YouTubeUploadError("The video file is empty.")

        session_uri = load_pending(pending_path, pkg.video_path)
        offset = 0
        if session_uri is not None:
            try:
                offset, finished = self._query_status(session_uri, size)
                if finished is not None:
                    return self._video_id(finished)
            except UploadSessionExpired:
                clear_pending(pending_path)
                session_uri, offset = None, 0
        if session_uri is None:
            session_uri = self._start_session(pkg, size, is_cancelled)
            save_pending(pending_path, pkg.video_path, session_uri, pkg.title)

        return self._send_chunks(
            pkg.video_path, session_uri, size, offset, on_progress, is_cancelled)

    def set_thumbnail(self, video_id: str, path: Path) -> None:
        content_type = _THUMBNAIL_TYPES.get(path.suffix.lower())
        if content_type is None:
            raise ThumbnailError("The cover must be a PNG or JPEG image.")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ThumbnailError(f"The cover could not be read: {exc.strerror or exc}") from exc
        try:
            response = self._with_retries(lambda: self._once(
                "post", THUMBNAIL_URL, headers={"Content-Type": content_type},
                params={"videoId": video_id, "uploadType": "media"}, data=data))
        except YouTubeUploadError as exc:
            raise ThumbnailError(str(exc)) from exc
        if response.status_code != 200:
            reason = _error_reason(response)
            if response.status_code == 403:
                raise ThumbnailError(
                    "YouTube refused the cover; custom covers need a channel verified by phone.")
            raise ThumbnailError(
                f"YouTube rejected the cover ({response.status_code} {reason})".strip())

    def update_metadata(self, video_id: str, pkg: PublishPackage) -> None:
        """Replace an uploaded video's title, description and tags (its
        ``snippet``) with *pkg*'s. The video, its cover and its privacy are
        left as they are."""
        snippet = build_metadata(pkg)["snippet"]
        body = json.dumps({"id": video_id, "snippet": snippet}).encode("utf-8")
        response = self._with_retries(lambda: self._once(
            "put", VIDEOS_URL, headers={"Content-Type": "application/json; charset=UTF-8"},
            params={"part": "snippet"}, data=body))
        if response.status_code != 200:
            status, reason = response.status_code, _error_reason(response)
            if status == 404:
                raise YouTubeUploadError(f"YouTube has no video {video_id} on this channel.")
            if status == 403 and reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                raise QuotaExceeded("The YouTube API quota for today is used up.")
            raise YouTubeUploadError(
                f"YouTube rejected the update ({status} {reason})".strip())

    # ----------------------------------------------------------- protocol

    def _start_session(self, pkg: PublishPackage, size: int, is_cancelled: Callable[[], bool]) -> str:
        response = self._with_retries(lambda: self._once(
            "post", UPLOAD_URL,
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/*",
                "X-Upload-Content-Length": str(size),
            },
            params={"uploadType": "resumable", "part": "snippet,status"},
            data=json.dumps(build_metadata(pkg)).encode("utf-8"),
        ), is_cancelled)
        if response.status_code != 200:
            self._raise_for(response)
        uri = response.headers.get("Location")
        if not uri:
            raise YouTubeUploadError("YouTube did not start an upload session.")
        return str(uri)

    def _query_status(self, session_uri: str, size: int) -> tuple[int, Optional[Any]]:
        """``(next offset, finished response or None)`` for a session."""
        response = self._with_retries(lambda: self._once(
            "put", session_uri, headers={"Content-Range": f"bytes */{size}"}, data=b""))
        if response.status_code in (200, 201):
            return size, response
        if response.status_code == 308:
            return _confirmed_offset(response), None
        self._raise_for(response, session=True)

    def _send_chunks(
        self,
        video: Path,
        session_uri: str,
        size: int,
        offset: int,
        on_progress: Callable[[int, int], None],
        is_cancelled: Callable[[], bool],
    ) -> str:
        failures = 0
        with video.open("rb") as fh:
            while True:
                if is_cancelled():
                    raise UploadCancelled("cancelled")
                on_progress(offset, size)
                fh.seek(offset)
                chunk = fh.read(self._chunk_size)
                if not chunk:
                    raise YouTubeUploadError("The video file ended before the upload finished.")
                end = offset + len(chunk) - 1
                try:
                    response = self._once(
                        "put", session_uri,
                        headers={"Content-Range": f"bytes {offset}-{end}/{size}"}, data=chunk)
                except _Retryable:
                    failures += 1
                    if failures > MAX_RETRIES:
                        raise YouTubeUploadError("The connection to YouTube kept failing.")
                    self._wait(failures, is_cancelled)
                    offset, finished = self._query_status(session_uri, size)
                    if finished is not None:
                        return self._video_id(finished)
                    continue
                failures = 0
                if response.status_code in (200, 201):
                    on_progress(size, size)
                    return self._video_id(response)
                if response.status_code == 308:
                    offset = _confirmed_offset(response)
                    continue
                self._raise_for(response, session=True)

    # ------------------------------------------------------------- plumbing

    def _once(
        self, method: str, url: str, *, headers: dict, data: bytes,
        params: Optional[dict] = None,
    ) -> Any:
        """One HTTP call with the bearer token; refreshes it once on a 401.
        Raises :class:`_Retryable` for network errors and 5xx answers."""
        response = None
        for attempt in range(2):
            merged = {"Authorization": f"Bearer {self._tokens.access_token()}", **headers}
            try:
                response = getattr(self._session, method)(
                    url, headers=merged, data=data, params=params, timeout=_REQUEST_TIMEOUT)
            except Exception as exc:
                logger.warning("YouTube request failed: %s", type(exc).__name__)
                raise _Retryable() from exc
            if response.status_code == 401 and attempt == 0:
                self._tokens.invalidate()
                continue
            break
        assert response is not None
        if response.status_code >= 500:
            raise _Retryable()
        return response

    def _with_retries(
        self, call: Callable[[], Any], is_cancelled: Callable[[], bool] = lambda: False,
    ) -> Any:
        for failures in range(1, MAX_RETRIES + 2):
            try:
                return call()
            except _Retryable:
                if failures > MAX_RETRIES:
                    break
                self._wait(failures, is_cancelled)
        raise YouTubeUploadError("The connection to YouTube kept failing.")

    def _wait(self, failures: int, is_cancelled: Callable[[], bool]) -> None:
        delay = min(2 ** (failures - 1), MAX_BACKOFF_SECONDS)
        remaining = float(delay)
        while remaining > 0:
            if is_cancelled():
                raise UploadCancelled("cancelled")
            step = min(remaining, 0.5)
            self._sleep(step)
            remaining -= step

    def _video_id(self, response: Any) -> str:
        try:
            return str(response.json()["id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise YouTubeUploadError("YouTube accepted the video but returned no id.") from exc

    def _raise_for(self, response: Any, *, session: bool = False) -> NoReturn:
        status = response.status_code
        reason = _error_reason(response)
        if status == 403:
            if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                raise QuotaExceeded("The YouTube API quota for today is used up.")
            if reason == "uploadLimitExceeded":
                raise UploadForbidden("The channel has reached its upload limit.")
            raise UploadForbidden(f"YouTube refused the upload ({reason or 'forbidden'}).")
        if session and status in (404, 410):
            raise UploadSessionExpired("The upload session has expired.")
        if status == 401:
            raise UploadForbidden("YouTube did not accept the login; connect the account again.")
        raise YouTubeUploadError(f"YouTube rejected the upload ({status} {reason})".strip())
