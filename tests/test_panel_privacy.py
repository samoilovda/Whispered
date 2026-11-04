"""MainWindow and the flow controllers call panels through their public
methods, not their private ones (audit 2026-11-04 AR1): a panel can change
its internals without a search through every controller."""

from __future__ import annotations

import re
from pathlib import Path

_PANELS = (
    "batch_panel", "book_panel", "file_selector", "start_view", "youtube_panel",
    "clips_view", "transcript_view", "cover_view", "status_bar", "library_view",
    "live_view", "recorder_widget", "course_capture_panel", "subtitle_view",
    "live_runtime", "cleaned_view", "article_view", "insights_panel", "cut_view",
    "chat_panel", "record_view",
)
_PATTERN = re.compile(r"\.(%s)\._[a-z][a-z_]*" % "|".join(_PANELS))


def test_no_private_panel_access_from_the_window_or_controllers():
    root = Path(__file__).resolve().parent.parent / "ui"
    found = [
        f"{path.name}: {match.group(0)}"
        for path in sorted(root.glob("*.py"))
        for match in _PATTERN.finditer(path.read_text(encoding="utf-8"))
    ]
    assert found == []
