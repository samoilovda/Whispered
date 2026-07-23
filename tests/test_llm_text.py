"""Unit tests for core/llm_text.py"""

# Qt and core.lm_client/core.ai_worker stand-ins come from tests/conftest.py.
import pytest

from core.llm_text import sample_lines_evenly, split_into_chunks


def test_split_into_chunks_preserves_text_boundaries_and_overlap():
    text = "Sentence one. Sentence two. Sentence three. Sentence four."
    chunks = split_into_chunks(text, chunk_size=28, overlap=6, separators=(". ", " "))
    assert len(chunks) > 1
    assert all(chunks)
    assert chunks[0].startswith("Sentence one")
    assert "Sentence four" in chunks[-1]


@pytest.mark.parametrize("chunk_size,overlap", [(0, 0), (10, -1), (10, 10)])
def test_split_into_chunks_rejects_invalid_sizes(chunk_size, overlap):
    with pytest.raises(ValueError):
        split_into_chunks("text " * 10, chunk_size, overlap)


# ── sample_lines_evenly ──────────────────────────────────────────────────────

def test_sample_short_lines_unchanged():
    lines = [f"line {i}" for i in range(5)]
    assert sample_lines_evenly(lines, 1000) == "\n".join(lines)


def test_sample_never_exceeds_limit():
    lines = [f"[{i * 10}s] " + "t" * 100 for i in range(200)]
    for limit in (200, 500, 2000, 5000):
        result = sample_lines_evenly(lines, limit)
        assert len(result) <= limit, f"limit={limit}: got {len(result)}"


def test_sample_keeps_first_and_last_lines():
    """The whole point of even sampling over tail-truncation: both ends of a
    long transcript must survive, not just the beginning."""
    lines = [f"[{i * 10}s] segment number {i}" for i in range(300)]
    result = sample_lines_evenly(lines, 2000, keep_edges=10)
    assert lines[0] in result
    assert lines[-1] in result
    assert lines[1] in result
    assert lines[-2] in result


def test_sample_middle_is_represented_when_budget_allows():
    lines = [f"[{i * 10}s] segment number {i}" for i in range(300)]
    result = sample_lines_evenly(lines, 4000, keep_edges=10)
    # Not guaranteed to hit the exact middle index, but *some* middle content
    # (not just the two edges) must be present when there's budget for it.
    edges_only_len = len("\n".join(lines[:10] + lines[-10:]))
    assert len(result) > edges_only_len


def test_sample_empty_lines():
    assert sample_lines_evenly([], 100) == ""


# ── insights_worker integration ──────────────────────────────────────────────

def _make_segments(n: int, chars_each: int = 100) -> list[dict]:
    return [
        {"start": i * 10, "text": "t" * chars_each, "speaker": ""}
        for i in range(n)
    ]


def test_build_prompt_text_truncates_long_transcript(monkeypatch):
    """Long segment lists must produce a prompt within the transcript limit."""
    from core.insights_worker import _build_prompt_text
    monkeypatch.setattr("core.insights_worker.load_prompt", lambda *a, **kw: "SYS")

    max_chars = 500
    segs = _make_segments(100, chars_each=100)   # 10 000 chars of transcript
    prompt = _build_prompt_text("chapters", segs, max_transcript_chars=max_chars)

    # System prompt must survive intact
    assert prompt.startswith("SYS\n")

    # Transcript part must not exceed limit
    transcript_part = prompt[len("SYS\n"):]
    assert len(transcript_part) <= max_chars


def test_build_prompt_text_short_transcript_unchanged(monkeypatch):
    """Short transcripts must not be modified."""
    from core.insights_worker import _build_prompt_text
    monkeypatch.setattr("core.insights_worker.load_prompt", lambda *a, **kw: "")

    segs = _make_segments(2, chars_each=10)
    prompt = _build_prompt_text("chapters", segs, max_transcript_chars=48_000)
    assert "[truncated" not in prompt


def test_build_prompt_text_covers_both_ends_of_long_recording(monkeypatch):
    """A 2-hour recording's tail must not be silently dropped (F2): with
    segments spanning 0..7200s and a transcript budget too small to fit
    them all, the prompt must still contain text from both the first and
    the last few minutes."""
    from core.insights_worker import _build_prompt_text
    monkeypatch.setattr("core.insights_worker.load_prompt", lambda *a, **kw: "SYS")

    segs = [
        {"start": i * 10, "text": f"segment {i} content here", "speaker": ""}
        for i in range(720)   # 0s .. 7190s (~2 hours), one segment every 10s
    ]
    max_chars = 5000
    prompt = _build_prompt_text("chapters", segs, max_transcript_chars=max_chars)

    transcript_part = prompt[len("SYS\n"):]
    assert len(transcript_part) <= max_chars
    assert "segment 0 content" in transcript_part
    # Last segment starts at 7190s; its text must survive too.
    assert "segment 719 content" in transcript_part
