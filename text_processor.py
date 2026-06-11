"""
Whispered - Text Processing Module
AI-powered text cleaning and coherence processing using LM Studio
"""

import json
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

from core.lm_client import LMStudioClient
from core.logger import get_logger
from core.prompts import load_prompt
from config import get_config

logger = get_logger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

# Chunk size for processing long texts (in characters)
TEXT_CHUNK_SIZE = 8000
TEXT_CHUNK_OVERLAP = 500

# Per-request timeout (seconds) for LM Studio calls. Long transcripts on a busy
# local model can take minutes; this is passed explicitly so a stalled request
# fails predictably instead of hanging on an implicit default.
LM_REQUEST_TIMEOUT = 300


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class CleanedText:
    """Result of text cleaning operation."""
    original: str
    cleaned: str
    removed_fillers: int
    sentences_fixed: int
    paragraphs_created: int
    
    @property
    def improvement_ratio(self) -> float:
        """How much shorter the cleaned text is vs original."""
        if len(self.original) == 0:
            return 0.0
        return 1.0 - (len(self.cleaned) / len(self.original))


@dataclass
class CoherentText:
    """Result of coherence processing."""
    text: str
    paragraphs: list[str] = field(default_factory=list)
    topic_shifts: list[int] = field(default_factory=list)  # Paragraph indices where topic changes
    speaker_changes: list[int] = field(default_factory=list)  # Paragraph indices with speaker change


@dataclass
class ProcessingResult:
    """Complete text processing result."""
    original: str
    cleaned: CleanedText
    coherent: CoherentText
    processing_time: float = 0.0


# ============================================================================
# TEXT CLEANER
# ============================================================================

# Common filler words and phrases to remove
FILLER_PATTERNS = [
    "uh ", "um ", "uhm ", "er ", "ah ",
    "you know", "like ", "so ", "well ",
    "I mean ", "kind of ", "sort of ",
    "basically ", "actually ", "literally ",
    "right ", "okay so ", "and so ",
]

CLEANING_SYSTEM_PROMPT = load_prompt(
    "cleaning",
    fallback=(
        "You are a text editor specializing in cleaning spoken transcriptions.\n"
        "Your task is to transform raw speech into clean, readable text while preserving the original meaning.\n"
        "Do NOT summarize, add new information, or change the speaker's intent.\n"
        "Output ONLY the cleaned text, no explanations or meta-commentary."
    ),
)

CLEANING_PROMPT_TEMPLATE = """Clean this spoken transcription into readable text by:

1. REMOVE filler words: uh, um, er, ah, "you know", "like", "I mean", "kind of", "sort of"
2. FIX run-on sentences: Add periods, commas, and proper punctuation
3. REMOVE false starts and repetitions: "I think I think" → "I think"
4. ADD paragraph breaks where the topic changes (every 3-5 sentences typically)
5. KEEP all factual content, quotes, and the speaker's original meaning

Transcription:
---
{text}
---

Output the cleaned text only:"""


class TextCleaner:
    """Clean raw transcription text using LM Studio."""
    
    def __init__(self, lm_client: Optional[LMStudioClient] = None,
                 timeout: int = LM_REQUEST_TIMEOUT):
        self.lm_client = lm_client or LMStudioClient()
        self.timeout = timeout

    def _quick_clean(self, text: str) -> str:
        """Quick regex-based cleaning for when LM Studio is unavailable."""
        result = text
        
        # Remove common fillers (case-insensitive)
        for filler in FILLER_PATTERNS:
            result = result.replace(filler.lower(), " ")
            result = result.replace(filler.capitalize(), " ")
        
        # Clean up multiple spaces
        while "  " in result:
            result = result.replace("  ", " ")
        
        return result.strip()
    
    def _count_removed_fillers(self, original: str, cleaned: str) -> int:
        """Estimate number of filler words removed."""
        count = 0
        original_lower = original.lower()
        for filler in FILLER_PATTERNS:
            count += original_lower.count(filler.lower())
        return count
    
    def clean(
        self,
        text: str,
        use_ai: bool = True,
        on_progress: Optional[Callable[[int, str], None]] = None
    ) -> CleanedText:
        """
        Clean the raw transcription text.
        
        Args:
            text: Raw transcription text
            use_ai: Whether to use LM Studio for cleaning (falls back to regex if unavailable)
            on_progress: Optional callback for progress updates (percentage, message)
            
        Returns:
            CleanedText with original and cleaned versions
        """
        if on_progress:
            on_progress(0, "Starting text cleaning...")
        
        # Check if AI is available and requested
        if use_ai and self.lm_client.check_connection():
            cleaned = self._clean_with_ai(text, on_progress)
        else:
            if on_progress:
                on_progress(10, "LM Studio unavailable, using basic cleaning...")
            cleaned = self._quick_clean(text)
        
        # Calculate statistics
        removed_fillers = self._count_removed_fillers(text, cleaned)
        
        # Estimate sentences fixed (rough: count new periods added)
        original_periods = text.count('.')
        cleaned_periods = cleaned.count('.')
        sentences_fixed = max(0, cleaned_periods - original_periods)
        
        # Count paragraphs
        paragraphs_created = cleaned.count('\n\n') + 1
        
        if on_progress:
            on_progress(100, "Text cleaning complete")
        
        return CleanedText(
            original=text,
            cleaned=cleaned,
            removed_fillers=removed_fillers,
            sentences_fixed=sentences_fixed,
            paragraphs_created=paragraphs_created
        )
    
    def _clean_with_ai(
        self,
        text: str,
        on_progress: Optional[Callable[[int, str], None]] = None
    ) -> str:
        """Clean text using LM Studio AI."""
        
        # For short texts, process in one go
        if len(text) <= TEXT_CHUNK_SIZE:
            if on_progress:
                on_progress(20, "Processing with AI...")
            
            prompt = CLEANING_PROMPT_TEMPLATE.format(text=text)
            result = self.lm_client.chat_completion(
                prompt=prompt,
                system_prompt=CLEANING_SYSTEM_PROMPT,
                temperature=0.3,  # Lower temperature for more consistent cleaning
                timeout=self.timeout
            )

            return result if result else self._quick_clean(text)
        
        # For long texts, process in chunks
        chunks = self._split_into_chunks(text)
        cleaned_chunks = []
        
        for i, chunk in enumerate(chunks):
            progress = int(20 + (70 * i / len(chunks)))
            if on_progress:
                on_progress(progress, f"Processing chunk {i+1}/{len(chunks)}...")
            
            prompt = CLEANING_PROMPT_TEMPLATE.format(text=chunk)
            result = self.lm_client.chat_completion(
                prompt=prompt,
                system_prompt=CLEANING_SYSTEM_PROMPT,
                temperature=0.3,
                timeout=self.timeout
            )

            cleaned_chunks.append(result if result else self._quick_clean(chunk))
        
        return "\n\n".join(cleaned_chunks)
    
    def _split_into_chunks(self, text: str) -> list[str]:
        """Split text into overlapping chunks for processing."""
        chunks = []
        start = 0
        
        while start < len(text):
            end = start + TEXT_CHUNK_SIZE
            
            # Try to break at sentence boundary
            if end < len(text):
                # Look for sentence end near the chunk boundary
                for sep in ['. ', '.\n', '? ', '! ']:
                    pos = text.rfind(sep, start + TEXT_CHUNK_SIZE - 500, end)
                    if pos > start:
                        end = pos + len(sep)
                        break
            
            chunks.append(text[start:end].strip())
            start = end - TEXT_CHUNK_OVERLAP
        
        return chunks


# ============================================================================
# COHERENCE PROCESSOR
# ============================================================================

COHERENCE_SYSTEM_PROMPT = load_prompt(
    "coherence",
    fallback=(
        "You are a text structure specialist.\n"
        "Your task is to organize cleaned transcription text into logical, coherent paragraphs.\n"
        "Identify topic shifts and create natural paragraph breaks.\n"
        "Do NOT change the content, only reorganize into clear paragraphs."
    ),
)

COHERENCE_PROMPT_TEMPLATE = """Organize this text into logical paragraphs:

1. GROUP related sentences together
2. ADD paragraph breaks (blank lines) between different topics
3. IDENTIFY where the speaker changes topic (mark with [TOPIC SHIFT] if helpful)
4. PRESERVE all original content exactly
5. Make the text flow naturally and be easy to read

Text:
---
{text}
---

Output the organized text with clear paragraph breaks:"""


class CoherenceProcessor:
    """Process text for logical coherence and structure."""
    
    def __init__(self, lm_client: Optional[LMStudioClient] = None,
                 timeout: int = LM_REQUEST_TIMEOUT):
        self.lm_client = lm_client or LMStudioClient()
        self.timeout = timeout

    def process(
        self,
        text: str,
        use_ai: bool = True,
        on_progress: Optional[Callable[[int, str], None]] = None
    ) -> CoherentText:
        """
        Process text for logical coherence.
        
        Args:
            text: Cleaned text to process
            use_ai: Whether to use LM Studio
            on_progress: Optional progress callback
            
        Returns:
            CoherentText with structured paragraphs
        """
        if on_progress:
            on_progress(0, "Analyzing text structure...")
        
        if use_ai and self.lm_client.check_connection():
            processed = self._process_with_ai(text, on_progress)
        else:
            # Basic paragraph splitting
            processed = self._basic_paragraph_split(text)
        
        # Parse the result into paragraphs
        paragraphs = [p.strip() for p in processed.split('\n\n') if p.strip()]
        
        # Identify topic shifts (marked with [TOPIC SHIFT] or detected by keywords)
        topic_shifts = []
        for i, para in enumerate(paragraphs):
            if '[TOPIC SHIFT]' in para or any(
                para.lower().startswith(word) 
                for word in ['however', 'but', 'now', 'another', 'moving on', 'next']
            ):
                topic_shifts.append(i)
                # Remove the marker if present
                paragraphs[i] = para.replace('[TOPIC SHIFT]', '').strip()
        
        if on_progress:
            on_progress(100, "Structure analysis complete")
        
        return CoherentText(
            text='\n\n'.join(paragraphs),
            paragraphs=paragraphs,
            topic_shifts=topic_shifts,
            speaker_changes=[]  # Could be enhanced with speaker diarization
        )
    
    def _process_with_ai(
        self,
        text: str,
        on_progress: Optional[Callable[[int, str], None]] = None
    ) -> str:
        """Process coherence with AI."""
        if on_progress:
            on_progress(30, "Organizing paragraphs with AI...")
        
        prompt = COHERENCE_PROMPT_TEMPLATE.format(text=text)
        result = self.lm_client.chat_completion(
            prompt=prompt,
            system_prompt=COHERENCE_SYSTEM_PROMPT,
            temperature=0.3,
            timeout=self.timeout
        )

        return result if result else text
    
    def _basic_paragraph_split(self, text: str) -> str:
        """Basic paragraph splitting without AI."""
        sentences = []
        current = ""
        
        for char in text:
            current += char
            if char in '.!?' and len(current) > 10:
                sentences.append(current.strip())
                current = ""
        
        if current.strip():
            sentences.append(current.strip())
        
        # Group into paragraphs of ~4 sentences
        paragraphs = []
        for i in range(0, len(sentences), 4):
            para = ' '.join(sentences[i:i+4])
            paragraphs.append(para)
        
        return '\n\n'.join(paragraphs)


# ============================================================================
# MAIN PROCESSOR
# ============================================================================

class TextProcessor:
    """Main text processing pipeline combining cleaning and coherence."""
    
    def __init__(self, lm_studio_url: Optional[str] = None,
                 timeout: Optional[int] = None):
        # Fall back to user configuration so the AI panel honors the
        # configured LM Studio URL / timeout instead of hardcoded defaults.
        cfg = get_config()
        url = lm_studio_url or cfg.lm_studio_url
        resolved_timeout = timeout if timeout is not None else \
            getattr(cfg, 'lm_request_timeout', LM_REQUEST_TIMEOUT)
        self.lm_client = LMStudioClient(url)
        self.cleaner = TextCleaner(self.lm_client, timeout=resolved_timeout)
        self.coherence = CoherenceProcessor(self.lm_client, timeout=resolved_timeout)
    
    def is_available(self) -> bool:
        """Check if LM Studio is available for processing."""
        return self.lm_client.check_connection()
    
    def get_model_name(self) -> Optional[str]:
        """Get the currently loaded model name."""
        return self.lm_client.get_loaded_model()
    
    def process(
        self,
        raw_text: str,
        use_ai: bool = True,
        on_progress: Optional[Callable[[int, str], None]] = None
    ) -> ProcessingResult:
        """
        Run the full text processing pipeline.
        
        Args:
            raw_text: Raw transcription text
            use_ai: Whether to use LM Studio (True) or basic processing (False)
            on_progress: Optional callback for progress updates
            
        Returns:
            ProcessingResult with all processing stages
        """
        import time
        start_time = time.time()
        
        # Stage 1: Clean text
        def clean_progress(pct, msg):
            if on_progress:
                on_progress(int(pct * 0.5), f"Cleaning: {msg}")
        
        cleaned = self.cleaner.clean(raw_text, use_ai=use_ai, on_progress=clean_progress)
        
        # Stage 2: Coherence processing
        def coherence_progress(pct, msg):
            if on_progress:
                on_progress(50 + int(pct * 0.5), f"Structuring: {msg}")
        
        coherent = self.coherence.process(
            cleaned.cleaned, 
            use_ai=use_ai, 
            on_progress=coherence_progress
        )
        
        processing_time = time.time() - start_time
        
        if on_progress:
            on_progress(100, f"Processing complete in {processing_time:.1f}s")
        
        return ProcessingResult(
            original=raw_text,
            cleaned=cleaned,
            coherent=coherent,
            processing_time=processing_time
        )


# ============================================================================
# CLI FOR TESTING
# ============================================================================

if __name__ == "__main__":
    import sys
    
    # Test with sample text
    sample = """uh well you know I think um the thing is that uh when you're working with 
    AI assistants you know they can be really helpful but um like sometimes they 
    don't quite get what you mean you know and uh so you have to be clear about 
    what you want and um like give them good context so basically the key is to 
    be specific about your needs"""
    
    processor = TextProcessor()
    
    print("Testing Text Processor")
    print("=" * 50)
    print(f"LM Studio available: {processor.is_available()}")
    
    if processor.is_available():
        print(f"Model: {processor.get_model_name()}")
    
    print("\nOriginal text:")
    print(sample)
    print("\n" + "=" * 50)
    
    result = processor.process(sample, use_ai=processor.is_available())
    
    print("\nCleaned text:")
    print(result.cleaned.cleaned)
    print(f"\nFillers removed: {result.cleaned.removed_fillers}")
    print(f"Sentences fixed: {result.cleaned.sentences_fixed}")
    print(f"Processing time: {result.processing_time:.2f}s")
