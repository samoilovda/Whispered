"""
Whispered - Book Pipeline
Processes a transcription through the "book" stages:
  1. Speech-to-prose unwrapping (расшивка) using LM Studio
  2. Optional: arbitrary user-provided prompt from a file

Output files are saved next to the source with a stage suffix.
If the output file already exists, a version suffix _v2, _v3, ... is added.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.lm_client import LMStudioClient
from core.prompts import load_prompt
from core.logger import get_logger
from config import get_config

logger = get_logger(__name__)

# Maximum characters per LM Studio request chunk
BOOK_CHUNK_SIZE = 6000
BOOK_CHUNK_OVERLAP = 300


# ============================================================================
# RESULT DATACLASS
# ============================================================================

@dataclass
class BookStageResult:
    """Result of a single pipeline stage."""
    stage: str            # e.g. "unwrap", "custom"
    output_text: str
    output_path: str      # path where file was saved
    success: bool = True
    error: str = ""


@dataclass
class BookResult:
    """Aggregated result of the full book pipeline."""
    source_path: str
    stages: list[BookStageResult] = field(default_factory=list)

    @property
    def final_text(self) -> str:
        """Return text from the last successful stage."""
        for stage in reversed(self.stages):
            if stage.success and stage.output_text:
                return stage.output_text
        return ""

    @property
    def success(self) -> bool:
        return all(s.success for s in self.stages)


# ============================================================================
# HELPERS
# ============================================================================

def _versioned_path(path: Path) -> Path:
    """
    If path already exists, return path_v2, path_v3, ... until a free name is found.
    """
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    # Strip existing _vN suffix to get a clean base
    base = re.sub(r"_v\d+$", "", stem)
    n = 2
    while True:
        candidate = parent / f"{base}_v{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _chunk_text(text: str, chunk_size: int = BOOK_CHUNK_SIZE,
                overlap: int = BOOK_CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks for large-document processing."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        # Try to break at paragraph/sentence boundary
        if end < len(text):
            for sep in ("\n\n", "\n", ". ", " "):
                pos = text.rfind(sep, start, end)
                if pos > start:
                    end = pos + len(sep)
                    break
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def _call_lm(
    client: LMStudioClient,
    text: str,
    system_prompt: str,
    temperature: float,
    is_cancelled: Optional[Callable[[], bool]],
    on_progress: Optional[Callable[[int, str], None]],
) -> Optional[str]:
    """Send text (possibly chunked) to LM Studio, return assembled result."""
    chunks = _chunk_text(text)
    total = len(chunks)
    results = []

    for i, chunk in enumerate(chunks):
        if is_cancelled and is_cancelled():
            return None

        if on_progress:
            pct = int((i / total) * 100)
            on_progress(pct, f"Обработка блока {i + 1}/{total}…")

        result = client.chat_completion(
            prompt=chunk,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=8192,
        )
        if result is None:
            logger.warning("LM Studio returned None for chunk %d/%d", i + 1, total)
            return None
        results.append(result.strip())

    return "\n\n".join(results)


# ============================================================================
# BOOK PIPELINE
# ============================================================================

class BookPipeline:
    """
    Runs the book processing pipeline stages in order.
    Each stage reads from the previous stage's output (or the initial transcript).
    All intermediate files are saved next to the source file.
    """

    def __init__(self) -> None:
        cfg = get_config()
        self._client = LMStudioClient(base_url=cfg.book_lm_url)
        self._temperature = cfg.book_temperature

    def is_available(self) -> bool:
        """Check if LM Studio is reachable."""
        return self._client.check_connection()

    # ------------------------------------------------------------------

    def process(
        self,
        transcript_text: str,
        source_path: str,
        do_unwrap: bool = True,
        do_custom: bool = False,
        custom_prompt_path: str = "",
        on_progress: Optional[Callable[[int, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> BookResult:
        """
        Run the book pipeline.

        Args:
            transcript_text: Raw transcription text (from .md or transcriber).
            source_path: Path to the original audio/md file (used for naming outputs).
            do_unwrap: If True, run speech-to-prose unwrapping.
            do_custom: If True, run the user-specified custom prompt.
            custom_prompt_path: Path to a .md file with the custom system prompt.
            on_progress: Callback (percentage: int, message: str).
            is_cancelled: Callback that returns True if user cancelled.

        Returns:
            BookResult with per-stage details.
        """
        result = BookResult(source_path=source_path)
        current_text = transcript_text
        source = Path(source_path)
        base_stem = source.stem

        # ---- Stage 1: Speech unwrapping (расшивка) ----
        if do_unwrap:
            if on_progress:
                on_progress(0, "Расшивка: отправляю в LM Studio…")

            system_prompt = load_prompt(
                "расшивка",
                fallback=(
                    "Ты редактор, превращающий устную речь в письменную. "
                    "Убери слова-паразиты, склей рыхлые предложения, сохрани содержание полностью. "
                    "Верни только переработанный текст без комментариев."
                ),
            )
            self._client.is_cancelled = is_cancelled

            output_text = _call_lm(
                self._client, current_text, system_prompt,
                self._temperature, is_cancelled, on_progress,
            )

            if is_cancelled and is_cancelled():
                return result

            if output_text:
                out_path = _versioned_path(source.parent / f"{base_stem}_расшито.md")
                try:
                    out_path.write_text(output_text, encoding="utf-8")
                    logger.info("Book pipeline: unwrap saved to %s", out_path)
                except Exception as e:
                    logger.error("Failed to save unwrap output: %s", e)
                    result.stages.append(BookStageResult(
                        stage="unwrap", output_text=output_text,
                        output_path=str(out_path), success=False, error=str(e),
                    ))
                    return result

                result.stages.append(BookStageResult(
                    stage="unwrap", output_text=output_text, output_path=str(out_path),
                ))
                current_text = output_text  # feed into next stage
            else:
                result.stages.append(BookStageResult(
                    stage="unwrap", output_text="", output_path="",
                    success=False, error="LM Studio вернул пустой результат",
                ))
                return result

        # ---- Stage 2: Custom user prompt ----
        if do_custom and custom_prompt_path:
            if on_progress:
                on_progress(0, "Пользовательский промпт: отправляю…")

            try:
                custom_system = Path(custom_prompt_path).read_text(encoding="utf-8").strip()
            except Exception as e:
                result.stages.append(BookStageResult(
                    stage="custom", output_text="", output_path="",
                    success=False, error=f"Не удалось прочитать файл промпта: {e}",
                ))
                return result

            self._client.is_cancelled = is_cancelled
            output_text = _call_lm(
                self._client, current_text, custom_system,
                self._temperature, is_cancelled, on_progress,
            )

            if is_cancelled and is_cancelled():
                return result

            if output_text:
                out_path = _versioned_path(source.parent / f"{base_stem}_custom.md")
                try:
                    out_path.write_text(output_text, encoding="utf-8")
                    logger.info("Book pipeline: custom stage saved to %s", out_path)
                except Exception as e:
                    result.stages.append(BookStageResult(
                        stage="custom", output_text=output_text,
                        output_path=str(out_path), success=False, error=str(e),
                    ))
                    return result

                result.stages.append(BookStageResult(
                    stage="custom", output_text=output_text, output_path=str(out_path),
                ))
            else:
                result.stages.append(BookStageResult(
                    stage="custom", output_text="", output_path="",
                    success=False, error="LM Studio вернул пустой результат",
                ))

        if on_progress:
            on_progress(100, "Готово")

        return result
