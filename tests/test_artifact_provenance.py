"""Unit tests for application/artifact_provenance.py (R5-full step 3, see
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from application.artifact_provenance import source_fingerprint, transcript_revision


# ---------------------------------------------------------------------------
# source_fingerprint
# ---------------------------------------------------------------------------

def test_no_source_path_returns_sentinel():
    assert source_fingerprint(None) == "no-source"
    assert source_fingerprint("") == "no-source"


def test_same_file_gives_same_fingerprint(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"fake audio data")
    assert source_fingerprint(str(f)) == source_fingerprint(str(f))


def test_different_content_gives_different_fingerprint(tmp_path):
    f = tmp_path / "audio.wav"
    f.write_bytes(b"short")
    first = source_fingerprint(str(f))
    f.write_bytes(b"a much longer replacement file content")
    second = source_fingerprint(str(f))
    assert first != second


def test_missing_file_does_not_raise(tmp_path):
    missing = tmp_path / "does-not-exist.wav"
    # Must not raise even though the file was never created.
    result = source_fingerprint(str(missing))
    assert isinstance(result, str) and result


# ---------------------------------------------------------------------------
# transcript_revision
# ---------------------------------------------------------------------------

@dataclass
class _FakeSegment:
    text: str


def test_same_text_gives_same_revision():
    segments = [_FakeSegment("hello"), _FakeSegment("world")]
    assert transcript_revision(segments, "en") == transcript_revision(segments, "en")


def test_edited_text_gives_different_revision():
    before = [_FakeSegment("hello world")]
    after = [_FakeSegment("hello GAMMA")]
    assert transcript_revision(before, "en") != transcript_revision(after, "en")


def test_different_language_gives_different_revision():
    segments = [_FakeSegment("hello world")]
    assert transcript_revision(segments, "en") != transcript_revision(segments, "ru")


def test_works_with_dict_segments():
    dict_segments = [{"text": "hello"}, {"text": "world"}]
    dataclass_segments = [_FakeSegment("hello"), _FakeSegment("world")]
    assert transcript_revision(dict_segments, "en") == transcript_revision(dataclass_segments, "en")


def test_empty_segments_does_not_raise():
    result = transcript_revision([], "en")
    assert isinstance(result, str) and result
