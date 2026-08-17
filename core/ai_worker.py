"""
Whispered - AI Processing Worker
Background QThread for AI-powered text cleaning and article generation.
Extracted from ui/main_window.py to separate compute from UI.
"""

from PyQt6.QtCore import pyqtSignal

from core.base_worker import BaseWorker
from core.logger import get_logger

logger = get_logger(__name__)


class AIProcessingWorker(BaseWorker):
    """Background worker for AI processing tasks (clean, generate, generate_all)."""

    progress = pyqtSignal(int, str)   # (percentage, message)
    finished = pyqtSignal(object)     # result object
    error = pyqtSignal(str)           # error message

    def _disconnect_business_signals(self) -> None:
        """WorkerRegistry hook (see core/worker_registry.py).

        The registry's generic by-name sweep deliberately skips any signal
        named ``finished`` so it never touches QThread's own lifecycle
        signal — but this class's ``finished`` is a business signal that
        happens to shadow it. Disconnect all three explicitly instead.
        """
        for signal in (self.progress, self.finished, self.error):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass

    def __init__(self, task: str, text: str, **kwargs) -> None:
        super().__init__()
        self.task: str = task
        self.text: str = text
        self.kwargs: dict = kwargs

    def _on_error(self, msg: str) -> None:
        logger.exception("AIProcessingWorker failed for task '%s'", self.task)
        self.error.emit(msg)

    def _execute(self) -> None:
        if self.task == "clean":
            self._run_clean()
        elif self.task == "generate":
            self._run_generate()
        elif self.task == "generate_all":
            self._run_generate_all()
        elif self.task == "book_unwrap":
            self._run_book_unwrap()
        else:
            self.error.emit(f"Unknown AI task: {self.task}")

    # ------------------------------------------------------------------

    def _run_clean(self) -> None:
        from text_processor import TextProcessor

        processor = TextProcessor()
        processor.lm_client.is_cancelled = self._cancelled.is_set

        def on_progress(pct: int, msg: str) -> None:
            if not self._cancelled.is_set():
                self.progress.emit(pct, msg)

        result = processor.process(self.text, use_ai=True, on_progress=on_progress)

        if not self._cancelled.is_set():
            self.finished.emit(result)

    def _run_generate(self) -> None:
        from article_generator import ArticleGenerator, ArticleFormat

        generator = ArticleGenerator()
        generator.lm_client.is_cancelled = self._cancelled.is_set
        format_key = self.kwargs.get('format', 'blog')
        format_enum = ArticleFormat(format_key)

        def on_progress(pct: int, msg: str) -> None:
            if not self._cancelled.is_set():
                self.progress.emit(pct, msg)

        article = generator.generate_article(
            self.text, format_enum, on_progress=on_progress
        )

        if not self._cancelled.is_set():
            self.finished.emit(article)

    def _run_generate_all(self) -> None:
        from article_generator import ArticleGenerator

        generator = ArticleGenerator()
        generator.lm_client.is_cancelled = self._cancelled.is_set

        def on_progress(pct: int, msg: str) -> None:
            if not self._cancelled.is_set():
                self.progress.emit(pct, msg)

        result = generator.generate_all_formats(
            self.text, on_progress=on_progress
        )

        if not self._cancelled.is_set():
            self.finished.emit(result)

    def _run_book_unwrap(self) -> None:
        """Run the book pipeline (unwrap + optional custom prompt)."""
        from book_pipeline import BookPipeline

        pipeline = BookPipeline()
        pipeline._client.is_cancelled = self._cancelled.is_set

        do_unwrap = self.kwargs.get('do_unwrap', True)
        do_custom = self.kwargs.get('do_custom', False)
        custom_prompt_path = self.kwargs.get('custom_prompt_path', '')
        source_path = self.kwargs.get('source_path', 'transcript')

        def on_progress(pct: int, msg: str) -> None:
            if not self._cancelled.is_set():
                self.progress.emit(pct, msg)

        result = pipeline.process(
            transcript_text=self.text,
            source_path=source_path,
            do_unwrap=do_unwrap,
            do_custom=do_custom,
            custom_prompt_path=custom_prompt_path,
            on_progress=on_progress,
            is_cancelled=self._cancelled.is_set,
        )

        if not self._cancelled.is_set():
            self.finished.emit(result)
