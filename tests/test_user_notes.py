"""application/user_notes.py and the notes' way into Insights (L1)."""

from application.user_notes import load_notes, notes_fingerprint, save_notes
from core.insights import _build_prompt_text
from domain.transcription import Segment


def test_save_load_and_blank_removes(tmp_path):
    save_notes(tmp_path, "[01:00] budget matters")
    assert load_notes(tmp_path) == "[01:00] budget matters"
    save_notes(tmp_path, "   ")
    assert load_notes(tmp_path) == ""
    assert not (tmp_path / "notes.md").exists()


def test_prompt_unchanged_without_notes_and_carries_them_with():
    segs = [Segment(0.0, 5.0, "hello there")]
    plain = _build_prompt_text("action_items", segs)
    assert _build_prompt_text("action_items", segs, notes="  ") == plain
    with_notes = _build_prompt_text("action_items", segs, notes="call the bank")
    assert "USER NOTES:\ncall the bank" in with_notes
    # Notes follow the shared transcript and precede the task instructions.
    assert with_notes.index("hello there") < with_notes.index("call the bank")
    assert with_notes.index("call the bank") < with_notes.index("action items")


def test_fingerprint_ignores_surrounding_whitespace():
    assert notes_fingerprint("a b\n") == notes_fingerprint("a b")
    assert notes_fingerprint("a b") != notes_fingerprint("a c")


def test_insights_cache_key_changes_with_notes(tmp_path):
    from application.steps import StepContext, _insights_prompt_version
    from domain.transcription import TranscriptionResult

    context = StepContext(
        source_path="", result=TranscriptionResult(segments=[Segment(0, 1, "x")], language="en", duration=1),
        record_id=1, artifact_dir=tmp_path,
    )
    before = _insights_prompt_version(context)
    save_notes(tmp_path, "focus on hiring")
    after = _insights_prompt_version(context)
    assert before != after and after.startswith(before)


def test_title_suggestion_prefers_the_users_pick(tmp_path):
    import json

    from application.user_notes import suggest_record_title

    assert suggest_record_title(tmp_path) == ""
    (tmp_path / "youtube_package.json").write_text(
        json.dumps({"yt_titles": ["1. First idea", "5 kinds of rest"]}), encoding="utf-8",
    )
    assert suggest_record_title(tmp_path) == "First idea"
    (tmp_path / "youtube_package.json").write_text(
        json.dumps({"yt_titles": ["5 kinds of rest"]}), encoding="utf-8",
    )
    assert suggest_record_title(tmp_path) == "5 kinds of rest"
    (tmp_path / "youtube_package.user.json").write_text(
        json.dumps({"title": "Second idea"}), encoding="utf-8",
    )
    assert suggest_record_title(tmp_path) == "Second idea"
