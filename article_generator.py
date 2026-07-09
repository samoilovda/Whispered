"""
Whisper Fedora - Article Generator Module
Multi-format article generation from processed transcriptions using LM Studio
"""

import json
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

from core.lm_client import LMStudioClient
from core.prompts import load_prompt


# ============================================================================
# ARTICLE FORMATS
# ============================================================================

class ArticleFormat(Enum):
    """Available article output formats."""
    BLOG_POST = "blog"
    FAQ = "faq"
    LISTICLE = "listicle"
    SUMMARY = "summary"
    SOCIAL = "social"


# Format metadata for UI
ARTICLE_FORMAT_INFO = {
    ArticleFormat.BLOG_POST: {
        "name": "📝 Blog Post",
        "description": "Full article with introduction, sections, and conclusion",
        "icon": "📝"
    },
    ArticleFormat.FAQ: {
        "name": "❓ FAQ",
        "description": "Question and answer format extracted from content",
        "icon": "❓"
    },
    ArticleFormat.LISTICLE: {
        "name": "📋 Listicle",
        "description": "Numbered list of key points and insights",
        "icon": "📋"
    },
    ArticleFormat.SUMMARY: {
        "name": "📄 Summary",
        "description": "Brief executive summary (2-3 paragraphs)",
        "icon": "📄"
    },
    ArticleFormat.SOCIAL: {
        "name": "📱 Social",
        "description": "Short snippets for social media (under 280 chars)",
        "icon": "📱"
    }
}


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class TopicAnalysis:
    """Extracted topics from text."""
    main_topics: list[str] = field(default_factory=list)
    key_insights: list[str] = field(default_factory=list)
    notable_quotes: list[str] = field(default_factory=list)
    suggested_titles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "topics": self.main_topics,
            "insights": self.key_insights,
            "quotes": self.notable_quotes,
            "titles": self.suggested_titles
        }


@dataclass
class Article:
    """Generated article content."""
    title: str
    format: ArticleFormat
    content: str
    topics: list[str] = field(default_factory=list)
    word_count: int = 0
    quality_score: float = 0.0

    def __post_init__(self):
        if self.word_count == 0:
            self.word_count = len(self.content.split())


@dataclass
class GenerationResult:
    """Result of article generation."""
    source_text: str
    topic_analysis: TopicAnalysis
    articles: list[Article] = field(default_factory=list)
    generation_time: float = 0.0


# ============================================================================
# CHUNKING (long transcripts must not be silently truncated)
# ============================================================================

def _split_into_chunks(text: str, chunk_size: int, overlap: int = 300) -> list[str]:
    """Split text into overlapping chunks, breaking at sentence boundaries where possible."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            for sep in ['. ', '.\n', '? ', '! ']:
                pos = text.rfind(sep, max(start, start + chunk_size - 500), end)
                if pos > start:
                    end = pos + len(sep)
                    break
        chunks.append(text[start:end].strip())
        start = end - overlap

    return chunks


def _merge_topic_analyses(analyses: list[TopicAnalysis]) -> TopicAnalysis:
    """Combine per-chunk topic analyses into one, de-duplicating near-identical entries."""

    def dedup(items: list[str], cap: int) -> list[str]:
        seen = set()
        out = []
        for item in items:
            cleaned = item.strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                out.append(cleaned)
        return out[:cap]

    return TopicAnalysis(
        main_topics=dedup([t for a in analyses for t in a.main_topics], 10),
        key_insights=dedup([i for a in analyses for i in a.key_insights], 8),
        notable_quotes=dedup([q for a in analyses for q in a.notable_quotes], 6),
        suggested_titles=dedup([t for a in analyses for t in a.suggested_titles], 4),
    )


# ============================================================================
# PROMPTS
# ============================================================================

TOPIC_EXTRACTION_PROMPT = load_prompt(
    "topic_extraction",
    fallback="""Analyze this transcription and extract key information.

Transcription:
---
{text}
---

Respond ONLY with valid JSON in this exact format:
{{
  "topics": ["topic1", "topic2", "topic3"],
  "insights": ["key insight 1", "key insight 2"],
  "quotes": ["notable quote 1", "notable quote 2"],
  "titles": ["suggested title 1", "suggested title 2"]
}}

Extract 3-7 main topics, 2-5 key insights, 2-4 notable quotes, and 2-3 suggested article titles.""",
)


BLOG_POST_PROMPT = load_prompt(
    "blog_post",
    fallback="""Write a blog post based on this content and topics.

Topics: {topics}
Key Insights: {insights}

Source Content:
---
{text}
---

Create a well-structured blog post with:
1. An engaging title (format: # Title)
2. Introduction paragraph that hooks the reader
3. 2-4 main sections with ## headers based on the topics
4. Key takeaways section (## Key Takeaways with bullet points)
5. Brief conclusion

Write in a conversational but professional style. Use markdown formatting.
Output ONLY the blog post, no meta-commentary.""",
)


FAQ_PROMPT = load_prompt(
    "faq",
    fallback="""Create an FAQ article from this content.

Source Content:
---
{text}
---

Generate 5-8 frequently asked questions with detailed answers based on the content.
Use this format:

# Frequently Asked Questions

## Q: [Question 1]?
[Detailed answer paragraph]

## Q: [Question 2]?
[Detailed answer paragraph]

...continue for all questions

Extract real questions and answers from the content. Do NOT invent information.""",
)


LISTICLE_PROMPT = load_prompt(
    "listicle",
    fallback="""Create a listicle article from this content.

Topics: {topics}

Source Content:
---
{text}
---

Create a numbered list article with 5-10 key points.
Format:

# [Number] [Topic] Tips/Insights/Lessons

1. **[Point Title]**
   [2-3 sentence explanation]

2. **[Point Title]**
   [2-3 sentence explanation]

...continue for all points

Each point should be actionable and insightful. Use markdown formatting.""",
)


SUMMARY_PROMPT = load_prompt(
    "summary",
    fallback="""Write an executive summary of this content.

Source Content:
---
{text}
---

Create a brief summary with:
- 1 paragraph overview (what this is about)
- 2-3 bullet points of main takeaways
- 1 paragraph conclusion/recommendation

Keep it under 300 words. Be concise but comprehensive.
Format with markdown (use **bold** for emphasis).""",
)


SOCIAL_PROMPT = load_prompt(
    "social",
    fallback="""Create social media snippets from this content.

Key Insights: {insights}
Notable Quotes: {quotes}

Source Content:
---
{text}
---

Generate 5 short social media posts:
- Each must be under 280 characters
- Make them engaging and shareable
- Include relevant emoji
- Vary the style (quote, insight, question, tip)

Format as a numbered list:
1. [first post]
2. [second post]
...etc""",
)


CONDENSE_PROMPT_TEMPLATE = """Summarize the key points and any quotable lines from this excerpt of a longer transcript. Be concise — plain bullet points only, no preamble or markdown headers:

---
{text}
---"""


QUALITY_SCORING_PROMPT = """Rate this article on a scale of 1-10 for each criterion:

Article:
---
{article}
---

Rate (respond with JSON only):
{{
  "clarity": [1-10 score],
  "structure": [1-10 score],
  "engagement": [1-10 score],
  "accuracy": [1-10 score],
  "overall": [1-10 average score]
}}"""


# ============================================================================
# ARTICLE GENERATOR
# ============================================================================

class ArticleGenerator:
    """Generate articles in multiple formats from transcribed text."""

    def __init__(self, lm_client: Optional[LMStudioClient] = None):
        self.lm_client = lm_client or LMStudioClient()

    def is_available(self) -> bool:
        """Check if LM Studio is available."""
        return self.lm_client.check_connection()

    _TOPIC_CHUNK_SIZE = 15000

    def extract_topics(
        self,
        text: str,
        on_progress: Optional[Callable[[int, str], None]] = None
    ) -> TopicAnalysis:
        """
        Extract topics and key information from text.

        Long transcripts are analyzed in overlapping chunks and merged, so
        content past the first _TOPIC_CHUNK_SIZE characters isn't silently
        dropped from the analysis (which is what a single-shot truncation
        would do for anything over ~15-20 minutes of speech).

        Args:
            text: Source text to analyze
            on_progress: Progress callback

        Returns:
            TopicAnalysis with extracted information
        """
        if on_progress:
            on_progress(10, "Extracting topics...")

        chunks = _split_into_chunks(text, self._TOPIC_CHUNK_SIZE)

        if len(chunks) == 1:
            return self._extract_topics_single(chunks[0])

        analyses = []
        for i, chunk in enumerate(chunks):
            if on_progress:
                pct = 10 + int(20 * i / len(chunks))
                on_progress(pct, f"Analyzing part {i + 1}/{len(chunks)}...")
            analyses.append(self._extract_topics_single(chunk))

        if on_progress:
            on_progress(30, "Merging analysis...")

        return _merge_topic_analyses(analyses)

    def _extract_topics_single(self, analysis_text: str) -> TopicAnalysis:
        """Extract topics from a single chunk that already fits in one prompt."""
        prompt = TOPIC_EXTRACTION_PROMPT.format(text=analysis_text)
        response = self.lm_client.chat_completion(
            prompt=prompt,
            temperature=0.5,
            max_tokens=1024
        )

        if response:
            try:
                # Clean up response - handle markdown code blocks
                clean_response = response.strip()
                if clean_response.startswith("```"):
                    clean_response = clean_response.split("```")[1]
                    if clean_response.startswith("json"):
                        clean_response = clean_response[4:]

                data = json.loads(clean_response)
                return TopicAnalysis(
                    main_topics=data.get("topics", []),
                    key_insights=data.get("insights", []),
                    notable_quotes=data.get("quotes", []),
                    suggested_titles=data.get("titles", [])
                )
            except json.JSONDecodeError:
                # Return basic analysis if parsing fails
                pass

        return TopicAnalysis(
            main_topics=["General Discussion"],
            key_insights=["Content analysis unavailable"],
            notable_quotes=[],
            suggested_titles=["Untitled Article"]
        )

    def generate_article(
        self,
        text: str,
        format: ArticleFormat,
        topics: Optional[TopicAnalysis] = None,
        on_progress: Optional[Callable[[int, str], None]] = None
    ) -> Article:
        """
        Generate a single article in the specified format.

        Args:
            text: Source text
            format: Article format to generate
            topics: Pre-extracted topics (optional, will extract if not provided)
            on_progress: Progress callback

        Returns:
            Generated Article
        """
        if on_progress:
            on_progress(0, f"Generating {format.value} article...")

        # Extract topics if not provided
        if topics is None:
            topics = self.extract_topics(text, on_progress)

        # Select prompt based on format
        prompt = self._get_format_prompt(text, format, topics)

        if on_progress:
            on_progress(40, f"Writing {format.value} content...")

        response = self.lm_client.chat_completion(
            prompt=prompt,
            temperature=0.7,
            max_tokens=4096
        )

        if not response:
            return Article(
                title="Generation Failed",
                format=format,
                content="Unable to generate article. Please check LM Studio connection.",
                topics=topics.main_topics
            )

        # Extract title from content (first # heading)
        title = self._extract_title(response, topics)

        if on_progress:
            on_progress(90, "Finalizing article...")

        article = Article(
            title=title,
            format=format,
            content=response,
            topics=topics.main_topics
        )

        if on_progress:
            on_progress(100, "Article complete")

        return article

    def generate_all_formats(
        self,
        text: str,
        formats: Optional[list[ArticleFormat]] = None,
        on_progress: Optional[Callable[[int, str], None]] = None
    ) -> GenerationResult:
        """
        Generate articles in multiple formats.

        Args:
            text: Source text
            formats: List of formats to generate (default: all)
            on_progress: Progress callback

        Returns:
            GenerationResult with all generated articles
        """
        import time
        start_time = time.time()

        if formats is None:
            formats = list(ArticleFormat)

        if on_progress:
            on_progress(0, "Starting article generation...")

        # Extract topics once for all articles
        topics = self.extract_topics(text, on_progress)

        articles = []
        total_formats = len(formats)

        for i, fmt in enumerate(formats):
            base_progress = 30 + int(60 * i / total_formats)

            def format_progress(pct, msg):
                if on_progress:
                    actual = base_progress + int(pct * 0.6 / total_formats)
                    on_progress(actual, msg)

            article = self.generate_article(text, fmt, topics, format_progress)
            articles.append(article)

        generation_time = time.time() - start_time

        if on_progress:
            on_progress(100, f"Generated {len(articles)} articles in {generation_time:.1f}s")

        return GenerationResult(
            source_text=text,
            topic_analysis=topics,
            articles=articles,
            generation_time=generation_time
        )

    def score_quality(self, article: Article) -> float:
        """
        Score article quality using LLM.

        Args:
            article: Article to score

        Returns:
            Quality score (0-10)
        """
        prompt = QUALITY_SCORING_PROMPT.format(article=article.content[:3000])
        response = self.lm_client.chat_completion(
            prompt=prompt,
            temperature=0.3,
            max_tokens=256
        )

        if response:
            try:
                clean_response = response.strip()
                if clean_response.startswith("```"):
                    clean_response = clean_response.split("```")[1]
                    if clean_response.startswith("json"):
                        clean_response = clean_response[4:]

                data = json.loads(clean_response)
                score = data.get("overall", 5.0)
                article.quality_score = float(score)
                return float(score)
            except (json.JSONDecodeError, ValueError):
                pass

        return 5.0  # Default middle score

    def _condense_for_prompt(self, text: str, max_chars: int) -> str:
        """Fit long text under max_chars without dropping its second half.

        A hard truncation silently discards everything past max_chars, which
        for a long transcript can mean the entire back half of a recording
        never reaches the model. Instead, chunk the text and ask the LLM for
        a short digest of each chunk, then concatenate — so the final prompt
        reflects the whole document, just condensed.
        """
        if len(text) <= max_chars:
            return text

        chunks = _split_into_chunks(text, max_chars)
        digests = []
        for chunk in chunks:
            prompt = CONDENSE_PROMPT_TEMPLATE.format(text=chunk)
            result = self.lm_client.chat_completion(prompt=prompt, temperature=0.3, max_tokens=400)
            digests.append(result.strip() if result else chunk[:500])

        combined = "\n\n".join(digests)
        return combined[:max_chars] if len(combined) > max_chars else combined

    def _get_format_prompt(
        self,
        text: str,
        format: ArticleFormat,
        topics: TopicAnalysis
    ) -> str:
        """Get the appropriate prompt for the format."""

        max_text_len = 12000
        analysis_text = self._condense_for_prompt(text, max_text_len)

        if format == ArticleFormat.BLOG_POST:
            return BLOG_POST_PROMPT.format(
                topics=", ".join(topics.main_topics),
                insights="\n".join(f"- {i}" for i in topics.key_insights),
                text=analysis_text
            )

        elif format == ArticleFormat.FAQ:
            return FAQ_PROMPT.format(text=analysis_text)

        elif format == ArticleFormat.LISTICLE:
            return LISTICLE_PROMPT.format(
                topics=", ".join(topics.main_topics),
                text=analysis_text
            )

        elif format == ArticleFormat.SUMMARY:
            return SUMMARY_PROMPT.format(text=analysis_text)

        elif format == ArticleFormat.SOCIAL:
            return SOCIAL_PROMPT.format(
                insights="\n".join(f"- {i}" for i in topics.key_insights),
                quotes="\n".join(f'"{q}"' for q in topics.notable_quotes),
                # Condense the already-condensed digest further rather than
                # re-processing the raw text a second time.
                text=self._condense_for_prompt(analysis_text, 5000)
            )

        return f"Summarize this text:\n{analysis_text}"

    def _extract_title(self, content: str, topics: TopicAnalysis) -> str:
        """Extract or generate title from content."""
        lines = content.strip().split('\n')

        for line in lines[:5]:  # Check first 5 lines
            line = line.strip()
            if line.startswith('# ') and not line.startswith('## '):
                return line[2:].strip()

        # Fall back to suggested titles or first topic
        if topics.suggested_titles:
            return topics.suggested_titles[0]
        if topics.main_topics:
            return topics.main_topics[0]

        return "Untitled Article"


# ============================================================================
# EXPORT FUNCTIONS
# ============================================================================

def export_article_md(article: Article, filepath: str) -> None:
    """Export article as Markdown file."""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(article.content)


def export_article_html(article: Article, filepath: str) -> None:
    """Export article as basic HTML file."""
    # Simple markdown to HTML conversion
    import re

    html_content = article.content

    # Headers
    html_content = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html_content, flags=re.MULTILINE)
    html_content = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html_content, flags=re.MULTILINE)
    html_content = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html_content, flags=re.MULTILINE)

    # Bold and italic
    html_content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html_content)
    html_content = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html_content)

    # Paragraphs
    paragraphs = html_content.split('\n\n')
    html_paragraphs = []
    for p in paragraphs:
        p = p.strip()
        if p and not p.startswith('<h'):
            if p.startswith('-') or p.startswith('*'):
                # List item
                items = [item.strip()[1:].strip() for item in p.split('\n') if item.strip()]
                p = '<ul>\n' + '\n'.join(f'<li>{item}</li>' for item in items) + '\n</ul>'
            elif p[0].isdigit() and p[1] == '.':
                # Numbered list
                items = [item.strip().split('.', 1)[1].strip() for item in p.split('\n') if item.strip()]
                p = '<ol>\n' + '\n'.join(f'<li>{item}</li>' for item in items) + '\n</ol>'
            else:
                p = f'<p>{p}</p>'
        html_paragraphs.append(p)

    html_content = '\n\n'.join(html_paragraphs)

    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{article.title}</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 800px; margin: 0 auto; padding: 2rem; line-height: 1.6; }}
        h1, h2, h3 {{ color: #1a1a1a; }}
        h1 {{ border-bottom: 2px solid #6366f1; padding-bottom: 0.5rem; }}
        blockquote {{ border-left: 4px solid #6366f1; padding-left: 1rem; color: #666; }}
        ul, ol {{ padding-left: 2rem; }}
        li {{ margin-bottom: 0.5rem; }}
    </style>
</head>
<body>
{html_content}
</body>
</html>"""

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(html_template)


def export_all_articles(articles: list[Article], directory: str) -> list[str]:
    """Export all articles to a directory. Returns list of created files."""
    import os
    os.makedirs(directory, exist_ok=True)

    created_files = []

    for article in articles:
        # Create safe filename
        safe_title = "".join(c if c.isalnum() or c in ' -_' else '_' for c in article.title)
        safe_title = safe_title[:50].strip()

        md_path = os.path.join(directory, f"{safe_title}_{article.format.value}.md")
        export_article_md(article, md_path)
        created_files.append(md_path)

    return created_files


# ============================================================================
# CLI FOR TESTING
# ============================================================================

if __name__ == "__main__":
    # Test with sample text
    sample = """
    Today we're going to talk about productivity and how to manage your time better.
    The first key insight is that you need to prioritize your tasks. Not everything
    is equally important. Focus on the high-impact activities first.

    Another important point is about taking breaks. Many people think working non-stop
    is productive, but actually your brain needs rest to perform at its best.

    Finally, consider using tools and systems to organize your work. Whether it's
    a simple to-do list or a complex project management system, having a structure
    helps you stay on track and reduces mental overhead.
    """

    generator = ArticleGenerator()

    print("Testing Article Generator")
    print("=" * 50)
    print(f"LM Studio available: {generator.is_available()}")

    if generator.is_available():
        print("\nExtracting topics...")
        topics = generator.extract_topics(sample)
        print(f"Topics: {topics.main_topics}")
        print(f"Insights: {topics.key_insights}")

        print("\nGenerating summary article...")
        article = generator.generate_article(sample, ArticleFormat.SUMMARY, topics)
        print(f"\nTitle: {article.title}")
        print(f"Word count: {article.word_count}")
        print("\nContent:")
        print(article.content)
    else:
        print("LM Studio not available. Start LM Studio to test article generation.")
