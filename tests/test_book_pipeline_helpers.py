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
