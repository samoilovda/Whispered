"""The host's approved/edited YouTube texts from other records, shown to
the model as style examples (application/style_examples.py, core.insights)."""

import json
import os
from pathlib import Path

from application.steps import STEP_REGISTRY, StepContext
from application.style_examples import (
    MAX_CHARS,
    collect_style_examples,
    description_text,
)
from core.insights import _build_prompt_text
from domain.transcription import Segment, TranscriptionResult

DESCRIPTION = (
    "Why does slowing down feel like losing? A question about speed.\n\n"
    "We talked with Ann about her year without a phone. We also touched on sleep.\n\n"
    "Timecodes:\n0:00 Intro\n1:02 The phone\n\n--------\n\nI am the host.\n\n#slow #life"
)


def _write(folder: Path, name: str, data: dict, mtime: float) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _queued(folder: Path, title: str, mtime: float, description: str = DESCRIPTION) -> None:
    _write(folder, "youtube_upload.queued.json",
           {"title": title, "description": description, "video_path": "v.mp4"}, mtime)


def test_description_text_drops_timecodes_footer_and_hashtags():
    assert description_text(DESCRIPTION) == (
        "Why does slowing down feel like losing? A question about speed.\n\n"
        "We talked with Ann about her year without a phone. We also touched on sleep."
    )


def test_most_recent_records_except_the_current_one(tmp_path):
    for n in range(1, 6):
        _queued(tmp_path / f"talk-{n}", f"Title {n}", mtime=1000 + n)
    examples = collect_style_examples(tmp_path, exclude_record_id=5)
    assert examples.titles == ("Title 4", "Title 3", "Title 2")
    assert len(examples.descriptions) == 3


def test_queued_package_wins_over_drafts_and_panel_pick(tmp_path):
    _queued(tmp_path / "a-1", "Approved", mtime=100)
    _write(tmp_path / "a-1", "youtube_publish.draft.json", {"title": "Draft"}, 200)
    _write(tmp_path / "b-2", "youtube_publish.draft.json",
           {"title": "Wizard edit", "title_base": "x", "description": DESCRIPTION}, 300)
    _write(tmp_path / "c-3", "youtube_package.user.json", {"title": "Picked"}, 50)
    _write(tmp_path / "d-4", "youtube_package.user.json", {"offset": 2}, 400)
    examples = collect_style_examples(tmp_path)
    assert examples.titles == ("Wizard edit", "Approved", "Picked")
    assert len(examples.descriptions) == 2


def test_examples_are_capped_at_sentence_ends(tmp_path):
    long = " ".join(f"Sentence number {n} is here." for n in range(200))
    for n in range(1, 4):
        _queued(tmp_path / f"t-{n}", f"Title {n}", mtime=n, description=long)
    examples = collect_style_examples(tmp_path)
    assert sum(len(d) for d in examples.descriptions) <= MAX_CHARS
    assert all(d.endswith("here.") for d in examples.descriptions)


def test_no_folder_no_examples(tmp_path):
    examples = collect_style_examples(tmp_path / "missing")
    assert not examples and examples.fingerprint() == "" and examples.for_insights() == {}


def test_prompt_keeps_transcript_first_and_shows_examples_per_type():
    segments = [Segment(0.0, 5.0, "We talked about phones")]
    examples = {"yt_titles": ["My Old Title"], "yt_description": ["My old hook."]}
    titles = _build_prompt_text("yt_titles", segments, style_examples=examples)
    assert titles.startswith("TRANSCRIPT")
    assert titles.index("We talked about phones") < titles.index("My Old Title")
    assert "My old hook." not in titles
    review = _build_prompt_text("yt_titles_review", segments, style_examples=examples)
    assert "My Old Title" in review
    tags = _build_prompt_text("yt_tags", segments, style_examples=examples)
    assert tags == _build_prompt_text("yt_tags", segments)


def _context(tmp_path: Path, **params) -> StepContext:
    result = TranscriptionResult(segments=[Segment(0.0, 1.0, "Hi")], language="en", duration=1.0)
    return StepContext(source_path=str(tmp_path / "talk.mp3"), result=result, record_id=7,
                       artifact_dir=tmp_path / "talk-7", params=params)


def test_new_examples_change_the_youtube_prompt_version(tmp_path):
    make = STEP_REGISTRY["youtube_package"].make_artifact
    before = make(_context(tmp_path, yt_style_examples=True)).prompt_version
    _queued(tmp_path / "talk-7", "Own record", mtime=1)
    assert make(_context(tmp_path, yt_style_examples=True)).prompt_version == before
    _queued(tmp_path / "other-3", "Another record", mtime=2)
    after = make(_context(tmp_path, yt_style_examples=True)).prompt_version
    assert after != before
    assert make(_context(tmp_path)).prompt_version == before
