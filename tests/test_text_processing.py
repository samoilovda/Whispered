"""Tests for pure text-processing logic (no LM Studio / network required)."""

from text_processor import TextCleaner, TEXT_CHUNK_SIZE, TEXT_CHUNK_OVERLAP
from book_pipeline import _chunk_text, _versioned_path


def test_quick_clean_removes_fillers_and_spaces():
    cleaner = TextCleaner()
    out = cleaner._quick_clean("so um this is uh a test you know")
    assert "um" not in out.split()
    assert "  " not in out  # collapsed double spaces


def test_count_removed_fillers():
    cleaner = TextCleaner()
    n = cleaner._count_removed_fillers("um um uh hello", "hello")
    assert n >= 3


def test_split_into_chunks_short_text_single_chunk():
    cleaner = TextCleaner()
    text = "Short text."
    chunks = cleaner._split_into_chunks(text)
    assert len(chunks) == 1
    assert chunks[0] == "Short text."


def test_split_into_chunks_long_text_covers_everything():
    cleaner = TextCleaner()
    # Build text longer than one chunk, with sentence boundaries.
    sentence = "This is a sentence. "
    text = sentence * (TEXT_CHUNK_SIZE // len(sentence) + 50)
    chunks = cleaner._split_into_chunks(text)
    assert len(chunks) > 1
    # Each chunk should be non-empty and not wildly exceed the chunk size.
    for c in chunks:
        assert c.strip()
        assert len(c) <= TEXT_CHUNK_SIZE + TEXT_CHUNK_OVERLAP


def test_book_chunk_text_short():
    assert _chunk_text("hello") == ["hello"]


def test_book_chunk_text_long_reassembles():
    para = "word " * 4000  # > BOOK_CHUNK_SIZE
    chunks = _chunk_text(para)
    assert len(chunks) > 1
    assert all(chunks)


def test_versioned_path_no_collision(tmp_path):
    p = tmp_path / "out.md"
    assert _versioned_path(p) == p


def test_versioned_path_with_collision(tmp_path):
    p = tmp_path / "out.md"
    p.write_text("x", encoding="utf-8")
    versioned = _versioned_path(p)
    assert versioned.name == "out_v2.md"
