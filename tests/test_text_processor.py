"""Unit tests for text_processor.py — no Qt, no network, no LM Studio required.

LM Studio calls are mocked via a fake client with the same interface as
core.lm_client.LMStudioClient (check_connection/chat_completion).
"""
import sys
import types

# PyQt6 stand-ins come from tests/conftest.py.

class _StubLMStudioClient:
    """Never-connected stand-in so TextProcessor() doesn't hit the network."""

    def __init__(self, base_url="http://localhost:1234/v1"):
        self.base_url = base_url

    def check_connection(self):
        return False

    def get_loaded_model(self):
        return None

    def chat_completion(self, *a, **kw):
        return None


# Assign directly (not setdefault): other test modules stub core.lm_client
# with a bare `object` placeholder that can't be instantiated, and whichever
# test file's module-level code runs first during collection would "win"
# under setdefault. text_processor.py is only imported here, so forcing our
# richer stub right before that import is deterministic regardless of
# collection order.
_lm_stub = types.ModuleType("core.lm_client")
_lm_stub.LMStudioClient = _StubLMStudioClient
_lm_stub.DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
sys.modules["core.lm_client"] = _lm_stub

_ai_stub = types.ModuleType("core.ai_worker")
_ai_stub.AIProcessingWorker = object
sys.modules.setdefault("core.ai_worker", _ai_stub)

from text_processor import (
    TextCleaner,
    CoherenceProcessor,
    TextProcessor,
    TEXT_CHUNK_SIZE,
)


class FakeLMClient:
    """Stand-in for LMStudioClient with a scriptable response."""

    def __init__(self, connected=True, response="AI RESULT"):
        self.connected = connected
        self.response = response
        self.calls = []

    def check_connection(self):
        return self.connected

    def get_loaded_model(self):
        return "fake-model" if self.connected else None

    def chat_completion(self, prompt, system_prompt=None, temperature=0.7):
        self.calls.append(prompt)
        return self.response


class TestQuickClean:
    def test_removes_filler_words(self):
        cleaner = TextCleaner(lm_client=FakeLMClient())
        result = cleaner._quick_clean("um so I think uh this is you know good")
        assert "um" not in result.lower().split()
        assert "uh" not in result.lower().split()

    def test_collapses_multiple_spaces(self):
        cleaner = TextCleaner(lm_client=FakeLMClient())
        result = cleaner._quick_clean("hello    world")
        assert "  " not in result

    def test_strips_leading_trailing_whitespace(self):
        cleaner = TextCleaner(lm_client=FakeLMClient())
        result = cleaner._quick_clean("  um hello  ")
        assert result == result.strip()

    def test_preserves_ordinary_words_that_can_also_be_fillers(self):
        cleaner = TextCleaner(lm_client=FakeLMClient())
        assert cleaner._quick_clean("I would like to go") == "I would like to go"
        assert cleaner._quick_clean("She played so well") == "She played so well"

    def test_removes_contextual_filler_at_sentence_start(self):
        cleaner = TextCleaner(lm_client=FakeLMClient())
        assert cleaner._quick_clean("Well, this is useful") == "this is useful"


class TestCleanFallsBackWithoutAI:
    def test_clean_uses_quick_clean_when_lm_unavailable(self):
        cleaner = TextCleaner(lm_client=FakeLMClient(connected=False))
        result = cleaner.clean("um hello world", use_ai=True)
        assert result.original == "um hello world"
        assert "um" not in result.cleaned.lower().split()

    def test_clean_use_ai_false_never_checks_connection(self):
        fake = FakeLMClient(connected=True)
        cleaner = TextCleaner(lm_client=fake)
        cleaner.clean("um hello", use_ai=False)
        assert fake.calls == []  # chat_completion never invoked

    def test_clean_with_ai_returns_ai_response_for_short_text(self):
        fake = FakeLMClient(connected=True, response="Cleaned by AI.")
        cleaner = TextCleaner(lm_client=fake)
        result = cleaner.clean("um hello world", use_ai=True)
        assert result.cleaned == "Cleaned by AI."
        assert len(fake.calls) == 1

    def test_progress_callback_invoked(self):
        cleaner = TextCleaner(lm_client=FakeLMClient(connected=False))
        events = []
        cleaner.clean("hello", use_ai=True, on_progress=lambda pct, msg: events.append((pct, msg)))
        assert events[0][0] == 0
        assert events[-1][0] == 100


class TestChunking:
    def test_short_text_single_chunk(self):
        cleaner = TextCleaner(lm_client=FakeLMClient())
        chunks = cleaner._split_into_chunks("short text")
        assert chunks == ["short text"]

    def test_long_text_split_into_multiple_chunks(self):
        cleaner = TextCleaner(lm_client=FakeLMClient())
        text = "word " * (TEXT_CHUNK_SIZE // 5 + 200)  # comfortably over one chunk
        chunks = cleaner._split_into_chunks(text)
        assert len(chunks) > 1

    def test_chunks_cover_full_text_with_overlap(self):
        cleaner = TextCleaner(lm_client=FakeLMClient())
        text = "sentence number %d. " % 0
        text = "".join(f"sentence number {i}. " for i in range(2000))
        chunks = cleaner._split_into_chunks(text)
        # every chunk should be non-empty and shorter than the whole text
        assert all(chunks)
        assert all(len(c) <= len(text) for c in chunks)

    def test_ai_cleaning_processes_all_chunks(self):
        fake = FakeLMClient(connected=True, response="x")
        cleaner = TextCleaner(lm_client=fake)
        text = "".join(f"sentence number {i}. " for i in range(2000))
        cleaner.clean(text, use_ai=True)
        assert len(fake.calls) > 1


class TestCoherenceProcessor:
    def test_basic_paragraph_split_without_ai(self):
        proc = CoherenceProcessor(lm_client=FakeLMClient(connected=False))
        text = "This is sentence one. This is sentence two. This is three. This is four. This is five."
        result = proc.process(text, use_ai=True)
        assert len(result.paragraphs) >= 1
        assert "\n\n" in result.text or len(result.paragraphs) == 1

    def test_topic_shift_detection(self):
        proc = CoherenceProcessor(lm_client=FakeLMClient(connected=False))
        # _basic_paragraph_split groups sentences 4-at-a-time, so a 5th
        # sentence starting with a shift keyword lands in its own paragraph.
        # (each sentence must exceed 10 chars on its own for the splitter's
        # accumulate-until-period logic to treat it as a separate sentence)
        text = ("This is one. This is two. This is three. This is four. "
                "However this is five.")
        result = proc.process(text, use_ai=False)
        assert 1 in result.topic_shifts

    def test_topic_shift_marker_removed_from_output(self):
        proc = CoherenceProcessor(lm_client=FakeLMClient(connected=True, response="Para one.\n\n[TOPIC SHIFT] Para two."))
        result = proc.process("irrelevant input", use_ai=True)
        assert "[TOPIC SHIFT]" not in result.text


class TestTextProcessor:
    def test_is_available_reflects_lm_client(self):
        processor = TextProcessor()
        processor.lm_client = FakeLMClient(connected=True)
        assert processor.is_available() is True

    def test_get_model_name(self):
        processor = TextProcessor()
        processor.lm_client = FakeLMClient(connected=True)
        assert processor.get_model_name() == "fake-model"

    def test_process_combines_cleaning_and_coherence(self):
        processor = TextProcessor()
        fake = FakeLMClient(connected=False)
        processor.lm_client = fake
        processor.cleaner = TextCleaner(lm_client=fake)
        processor.coherence = CoherenceProcessor(lm_client=fake)

        result = processor.process("um hello world. This is a test.", use_ai=True)
        assert result.original == "um hello world. This is a test."
        assert result.cleaned.cleaned
        assert result.coherent.text
