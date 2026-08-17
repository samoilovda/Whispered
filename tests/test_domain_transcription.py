"""domain/transcription.py must stay Qt-free (see R7-pre in
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md) and transcriber.py must re-export
the same objects rather than redefine them.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# tests/conftest.py installs Qt stand-ins under sys.modules — drop the
# cached transcriber module so this file always exercises the real one.
sys.modules.pop("transcriber", None)
sys.modules.pop("domain.transcription", None)

from domain.transcription import Segment, TranscriptionResult, Word
import transcriber


@pytest.mark.parametrize("module_path", ["domain/__init__.py", "domain/transcription.py"])
def test_domain_module_does_not_import_qt_or_ui_or_live(module_path):
    """Static AST check: nothing under domain/ may import PyQt6, ui, or
    core.live — that coupling is exactly what made transcriber.py's DTOs
    pull in Qt just by being imported."""
    src = Path(module_path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_prefixes = ("PyQt6", "ui.", "ui", "core.live")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith(forbidden_prefixes), (
                    f"{module_path} imports {alias.name!r} — domain/ must stay Qt-free"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith(forbidden_prefixes):
                pytest.fail(
                    f"{module_path}: 'from {node.module} import …' — domain/ must stay Qt-free"
                )


def test_transcriber_reexports_the_same_domain_objects():
    """transcriber.Segment/Word/TranscriptionResult must be the exact same
    classes as domain.transcription's — a re-export, not a parallel
    redefinition that would silently diverge."""
    assert transcriber.Segment is Segment
    assert transcriber.Word is Word
    assert transcriber.TranscriptionResult is TranscriptionResult


def test_transcription_result_full_text_and_speaker_label():
    result = TranscriptionResult(
        segments=[
            Segment(start=0.0, end=1.0, text=" hello ", speaker="Speaker 1"),
            Segment(start=1.0, end=2.0, text="world", speaker=None),
        ],
        language="en",
        duration=2.0,
        speaker_names={"Speaker 1": "Alice"},
    )
    assert result.full_text == "hello world"
    assert result.speaker_label("Speaker 1") == "Alice"
    assert result.speaker_label("Speaker 2") == "Speaker 2"
    assert result.speaker_label(None) is None


def test_segment_words_default_to_empty_list_independently():
    """Each Segment must get its own words list — a shared mutable default
    would leak appends from one segment into every other."""
    a = Segment(start=0.0, end=1.0, text="a")
    b = Segment(start=1.0, end=2.0, text="b")
    a.words.append(Word(start=0.0, end=0.5, text="a"))
    assert a.words != b.words
    assert b.words == []
