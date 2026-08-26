"""Unit tests for article_generator.py — no Qt, no network, no LM Studio required.

Covers the chunking/map-reduce fix for long transcripts: extract_topics() and
_get_format_prompt() used to silently truncate input to 12-15k characters,
which for a long recording meant the LLM never saw the second half of the
transcript. These tests pin the corrected behavior (full-document coverage
via chunk + merge/condense) so it can't silently regress back to truncation.
"""
import sys
import types

# PyQt6 stand-ins come from tests/conftest.py.


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
    Article,
    TopicAnalysis,
    _split_into_chunks,
    _merge_topic_analyses,
    export_article_html,
    export_all_articles,
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


class TestHtmlExport:
    def test_single_digit_paragraph_does_not_raise(self, tmp_path):
        article = Article(
            title="Test",
            format=ArticleFormat.SUMMARY,
            content="1",
            topics=[],
        )
        output = tmp_path / "article.html"

        export_article_html(article, str(output))

        assert "<p>1</p>" in output.read_text(encoding="utf-8")

    def test_html_export_escapes_model_generated_markup(self, tmp_path):
        article = Article(
            title="<unsafe>",
            format=ArticleFormat.SUMMARY,
            content="# Heading\n\n<script>alert('x')</script>",
        )
        output = tmp_path / "article.html"

        export_article_html(article, str(output))

        content = output.read_text(encoding="utf-8")
        assert "<script>" not in content
        assert "&lt;script&gt;" in content
        assert "<title>&lt;unsafe&gt;</title>" in content


class TestExportAllArticlesProvenance:
    """R5-full step 3: export_all_articles() optionally writes an Artifact
    manifest per exported .md (see docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md)."""

    @staticmethod
    def _article(fmt=ArticleFormat.SUMMARY, title="Test Article"):
        return Article(title=title, format=fmt, content="Some content.", topics=[])

    def test_without_provenance_kwargs_no_manifest_is_written(self, tmp_path):
        from infrastructure.persistence import artifact_store

        files = export_all_articles([self._article()], str(tmp_path))
        assert artifact_store.load(files[0]) is None

    def test_with_provenance_writes_a_manifest_per_article(self, tmp_path):
        from infrastructure.persistence import artifact_store

        articles = [self._article(ArticleFormat.SUMMARY), self._article(ArticleFormat.BLOG_POST)]
        files = export_all_articles(
            articles, str(tmp_path),
            record_id=7, source_path="/media/talk.mp4", source_hash="abc123",
            transcript_revision="rev-1",
        )
        assert len(files) == 2
        for f in files:
            artifact = artifact_store.load(f)
            assert artifact is not None
            assert artifact.record_id == "7"
            assert artifact.source_path == "/media/talk.mp4"
            assert artifact.source_hash == "abc123"
            assert artifact.transcript_revision == "rev-1"
            assert artifact.type.startswith("article_")

    def test_manifest_write_failure_does_not_prevent_the_md_file(self, tmp_path, monkeypatch):
        from pathlib import Path
        import infrastructure.persistence.artifact_store as artifact_store_module

        monkeypatch.setattr(
            artifact_store_module, "save",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        # Must not raise even though the manifest write fails internally.
        files = export_all_articles(
            [self._article()], str(tmp_path),
            record_id=1, transcript_revision="rev-1",
        )
        assert len(files) == 1
        assert Path(files[0]).exists()

    def test_partial_kwargs_are_treated_as_no_provenance(self, tmp_path):
        """record_id without transcript_revision (or vice versa) must not
        half-write a manifest with a placeholder — both are required."""
        from infrastructure.persistence import artifact_store

        files = export_all_articles([self._article()], str(tmp_path), record_id=1)
        assert artifact_store.load(files[0]) is None


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


class TestTopicExtractionSurvivesWrongShapedJson:
    def test_bare_array_falls_back_instead_of_raising(self):
        """A model answering the topics prompt with a JSON array parses
        fine, so data.get() raised AttributeError past the JSONDecodeError
        handler and failed the whole article step. It must degrade to the
        same fallback analysis a malformed response gets."""
        fake = FakeLMClient(responses='["Time management", "Focus"]')
        gen = ArticleGenerator(lm_client=fake)

        topics = gen._extract_topics_single("some transcript")

        assert topics.main_topics == ["General Discussion"]
        assert topics.suggested_titles == ["Untitled Article"]

    def test_object_response_is_still_read_normally(self):
        fake = FakeLMClient(
            responses='{"topics": ["Focus"], "titles": ["A title"]}'
        )
        gen = ArticleGenerator(lm_client=fake)

        topics = gen._extract_topics_single("some transcript")

        assert topics.main_topics == ["Focus"]
        assert topics.suggested_titles == ["A title"]


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
