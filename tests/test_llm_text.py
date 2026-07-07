"""Unit tests for core/llm_text.py"""

import sys
import types

for _mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui",
             "PyQt6.QtMultimedia"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

_qtcore = sys.modules["PyQt6.QtCore"]
_qtcore.QThread = type("QThread", (), {"start": lambda *a: None, "isRunning": lambda *a: False})
_qtcore.pyqtSignal = lambda *a, **kw: None
_qtcore.QObject = type("QObject", (), {"__init__": lambda *a, **kw: None})

_lm_stub = types.ModuleType("core.lm_client")
_lm_stub.LMStudioClient = object
_lm_stub.DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
sys.modules.setdefault("core.lm_client", _lm_stub)

_ai_stub = types.ModuleType("core.ai_worker")
_ai_stub.AIProcessingWorker = object
sys.modules.setdefault("core.ai_worker", _ai_stub)

from core.llm_text import fit_to_context


def test_short_text_unchanged():
    assert fit_to_context("hello", 100) == "hello"


def test_exact_limit_unchanged():
    text = "a" * 100
    assert fit_to_context(text, 100) == text


def test_long_text_truncated():
    text = "a" * 200
    result = fit_to_context(text, 100)
    assert len(result) <= 100


def test_truncation_includes_marker():
    text = "a" * 200
    marker = "\n[truncated]"
    result = fit_to_context(text, 100, marker=marker)
    assert result.endswith(marker)


def test_truncated_result_never_exceeds_limit():
    for limit in (10, 50, 100, 500):
        result = fit_to_context("x" * 1000, limit)
        assert len(result) <= limit, f"limit={limit}: got len={len(result)}"


def test_empty_text():
    assert fit_to_context("", 100) == ""


def test_marker_longer_than_limit():
    """When marker itself exceeds limit, result is capped at max_chars."""
    marker = "m" * 20
    result = fit_to_context("x" * 100, 10, marker=marker)
    assert len(result) <= 10


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
