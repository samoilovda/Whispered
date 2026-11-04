"""Regression tests for book-pipeline path allocation."""

from pathlib import Path

import pytest

from book_pipeline import _versioned_path


def test_versioned_path_limits_collision_search(tmp_path, monkeypatch):
    target = tmp_path / "book.md"
    target.write_text("base")
    monkeypatch.setattr(Path, "exists", lambda self: True)

    with pytest.raises(RuntimeError, match="Too many versions"):
        _versioned_path(target)


def test_a_failed_chunk_is_retried_once_not_the_whole_book():
    """Audit 2026-11-04 W06."""
    from book_pipeline import _call_lm

    class Client:
        def __init__(self, answers):
            self.answers = list(answers)
            self.calls = 0

        def chat_completion(self, **_kw):
            self.calls += 1
            return self.answers.pop(0)

    flaky = Client([None, "текст"])
    assert _call_lm(flaky, "короткий", "sys", 0.3, None, None) == "текст"
    assert flaky.calls == 2
    dead = Client([None, None])
    assert _call_lm(dead, "короткий", "sys", 0.3, None, None) is None
    assert dead.calls == 2
