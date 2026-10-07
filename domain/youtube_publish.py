"""Qt-free DTOs for publishing a finished recipe run to YouTube.

A ``PublishPackage`` is what the publish dialog hands to either the manual
hand-off actions (copy / reveal / open Studio) or the API uploader; an
``UploadRecord`` is what a successful API upload leaves behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PublishPackage:
    video_path: Path
    title: str
    description: str
    tags: tuple[str, ...]
    thumbnail_path: Optional[Path]
    privacy: str = "private"          # "private" | "unlisted"
    language: Optional[str] = None    # snippet.defaultLanguage / defaultAudioLanguage
    category_id: str = "22"           # People & Blogs
    made_for_kids: bool = False


@dataclass(frozen=True)
class UploadRecord:
    video_id: str
    uploaded_at: str                  # ISO-8601 UTC
    title: str
    privacy: str


@dataclass(frozen=True)
class PublishIssue:
    """One validation finding. ``key`` is an i18n key (``yt_publish_issue_*``)
    and ``params`` its format arguments; ``blocking`` issues disable upload."""

    kind: str
    key: str
    params: tuple[tuple[str, object], ...] = ()
    blocking: bool = True

    def format_params(self) -> dict[str, object]:
        return dict(self.params)
