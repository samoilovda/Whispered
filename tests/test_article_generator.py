"""Unit tests for article_generator.py — no Qt, no network, no LM Studio required.

Covers the chunking/map-reduce fix for long transcripts: extract_topics() and
_get_format_prompt() used to silently truncate input to 12-15k characters,
which for a long recording meant the LLM never saw the second half of the
transcript. These tests pin the corrected behavior (full-document coverage
via chunk + merge/condense) so it can't silently regress back to truncation.
"""
import sys
import types

for _mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui"):
    sys.modules.setdefault(_mod, types.ModuleType(_mod))


class _StubLMStudioClient:
    def __init__(self, base_url="http://localhost:1234/v1"):
        self.base_url = base_url

    def check_connection(self):
        return False

    def chat_completion(self, *a, **kw):
        return None


# Assign directly (not setdefault) — see tests/test_text_processor.py for why:
# other test modules stub core.lm_client with a bare `object` that can't be
# instantiated, and article_generator.py is only imported here.
_lm_stub = types.ModuleType("core.lm_client")
_lm_stub.LMStudioClient = _StubLMStudioClient
_lm_stub.DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
sys.modules["core.lm_client"] = _lm_stub

_ai_stub = types.ModuleType("core.ai_worker")
_ai_stub.AIProcessingWorker = object
sys.modules.setdefault("core.ai_worker", _ai_stub)

from article_generator import (
    ArticleGenerator,
    ArticleFormat,
    TopicAnalysis,
    _split_into_chunks,
    _merge_topic_analyses,
)


class FakeLMClient:
    """Scriptable stand-in for LMStudioClient; records every prompt it saw."""

    def __init__(self, responses=None):
        # responses: list consumed in order, or a single value reused for all calls
        self._responses = responses
        self._call_index = 0
        self.prompts = []

    def check_connection(self):
        return True

    def chat_completion(self, prompt, system_prompt=None, temperature=0.7, max_tokens=4096):
        self.prompts.append(prompt)
        if isinstance(self._responses, list):
            if self._call_index >= len(self._responses):
                return None
            resp = self._responses[self._call_index]
            self._call_index += 1
            return resp
        return self._responses


class TestSplitIntoChunks:
    def test_short_text_single_chunk(self):
        assert _split_into_chunks("hello world", 100) == ["hello world"]

    def test_long_text_split_into_multiple_chunks(self):
        text = "word " * 5000  # ~25000 chars
        chunks = _split_into_chunks(text, 12000)
        assert len(chunks) > 1
        assert all(chunks)

    def test_chunks_prefer_sentence_boundaries(self):
        text = "Sentence one is here. " * 1000
        chunks = _split_into_chunks(text, 12000)
        for chunk in chunks[:-1]:
            assert chunk.rstrip().endswith(('.', '?', '!'))


class TestMergeTopicAnalyses:
    def test_merges_and_dedupes_case_insensitively(self):
        a = TopicAnalysis(main_topics=["Time management", "Focus"],
                           key_insights=["Insight A"], notable_quotes=[], suggested_titles=["Title A"])
        b = TopicAnalysis(main_topics=["time management", "Rest"],
                           key_insights=["Insight A", "Insight B"], notable_quotes=[], suggested_titles=["Title B"])
        merged = _merge_topic_analyses([a, b])
        assert merged.main_topics == ["Time management", "Focus", "Rest"]
        assert merged.key_insights == ["Insight A", "Insight B"]

    def test_caps_output_size(self):
        analyses = [
            TopicAnalysis(main_topics=[f"topic{i}" for i in range(20)])
            for _ in range(3)
        ]
        merged = _merge_topic_analyses(analyses)
        assert len(merged.main_topics) <= 10


class TestExtractTopicsCoversFullTranscript:
    def test_short_text_single_call(self):
        fake = FakeLMClient(responses='{"topics": ["A"], "insights": [], "quotes": [], "titles": []}')
        gen = ArticleGenerator(lm_client=fake)
        gen.extract_topics("short transcript")
        assert len(fake.prompts) == 1

    def test_long_transcript_analyzes_every_chunk(self):
        # ~40000 chars — well over the 15000-char single-prompt limit that
        # used to be a hard truncation.
        long_text = "This is a sentence about the discussion. " * 1000
        assert len(long_text) > 30000

        responses = [
            '{"topics": ["chunk1-topic"], "insights": ["chunk1-insight"], "quotes": [], "titles": ["T1"]}',
            '{"topics": ["chunk2-topic"], "insights": ["chunk2-insight"], "quotes": [], "titles": ["T2"]}',
            '{"topics": ["chunk3-topic"], "insights": ["chunk3-insight"], "quotes": [], "titles": ["T3"]}',
        ]
        fake = FakeLMClient(responses=responses)
        gen = ArticleGenerator(lm_client=fake)
        result = gen.extract_topics(long_text)

        # Every chunk must have been sent to the LLM — this is the crux of
        # the fix: previously only the first ~15000 chars were ever seen.
        assert len(fake.prompts) >= 2
        # Topics gathered from more than one chunk should be present in the
        # merged result (proves the tail of the document wasn't dropped).
        assert "chunk1-topic" in result.main_topics
        assert any(t.startswith("chunk") for t in result.main_topics if t != "chunk1-topic")

    def test_progress_callback_reports_multiple_chunks(self):
        long_text = "Another sentence entirely. " * 1200
        fake = FakeLMClient(responses='{"topics": [], "insights": [], "quotes": [], "titles": []}')
        gen = ArticleGenerator(lm_client=fake)
        messages = []
        gen.extract_topics(long_text, on_progress=lambda pct, msg: messages.append(msg))
        assert any("part" in m.lower() for m in messages)


class TestCondenseForPrompt:
    def test_short_text_passthrough_no_llm_call(self):
        fake = FakeLMClient(responses="unused")
        gen = ArticleGenerator(lm_client=fake)
        result = gen._condense_for_prompt("short text", 1000)
        assert result == "short text"
        assert fake.prompts == []

    def test_long_text_condensed_via_all_chunks(self):
        long_text = "Detail about the topic at hand. " * 1000
        assert len(long_text) > 12000
        fake = FakeLMClient(responses=["digest one", "digest two", "digest three"])
        gen = ArticleGenerator(lm_client=fake)
        result = gen._condense_for_prompt(long_text, 12000)

        assert len(fake.prompts) >= 2
        assert "digest one" in result

    def test_condensed_result_fits_under_cap(self):
        long_text = "Content chunk text goes here. " * 2000
        # Every chunk "digest" comes back oversized, to exercise the
        # defensive final cap in _condense_for_prompt.
        fake = FakeLMClient(responses="x" * 5000)
        gen = ArticleGenerator(lm_client=fake)
        result = gen._condense_for_prompt(long_text, 8000)
        assert len(result) <= 8000


class TestGetFormatPromptUsesFullDocument:
    def test_summary_prompt_reflects_condensed_full_text(self):
        long_text = "Point about the meeting. " * 1500
        assert len(long_text) > 12000
        fake = FakeLMClient(responses=["first-half digest", "second-half digest"])
        gen = ArticleGenerator(lm_client=fake)
        topics = TopicAnalysis(main_topics=["x"], key_insights=[], notable_quotes=[], suggested_titles=[])
        prompt = gen._get_format_prompt(long_text, ArticleFormat.SUMMARY, topics)

        assert "first-half digest" in prompt
        assert "second-half digest" in prompt

    def test_short_text_not_condensed(self):
        fake = FakeLMClient(responses="unused")
        gen = ArticleGenerator(lm_client=fake)
        topics = TopicAnalysis()
        prompt = gen._get_format_prompt("a short transcript", ArticleFormat.SUMMARY, topics)
        assert "a short transcript" in prompt
        assert fake.prompts == []
