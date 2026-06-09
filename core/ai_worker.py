"""
Whispered - AI Processing Worker
Background QThread for AI-powered text cleaning and article generation.
Extracted from ui/main_window.py to separate compute from UI.
"""

from PyQt6.QtCore import QThread, pyqtSignal

from core.logger import get_logger

logger = get_logger(__name__)


class AIProcessingWorker(QThread):
    """Background worker for AI processing tasks (clean, generate, generate_all)."""

    progress = pyqtSignal(int, str)   # (percentage, message)
    finished = pyqtSignal(object)     # result object
    error = pyqtSignal(str)           # error message

    def __init__(self, task: str, text: str, **kwargs) -> None:
        super().__init__()
        self.task: str = task
        self.text: str = text
        self.kwargs: dict = kwargs
        self._cancelled: bool = False

    def run(self) -> None:
        """Execute the requested task in a background thread."""
        try:
            if self.task == "clean":
                self._run_clean()
            elif self.task == "generate":
                self._run_generate()
            elif self.task == "generate_all":
                self._run_generate_all()
            else:
                self.error.emit(f"Unknown AI task: {self.task}")
        except Exception as e:
            logger.exception("AIProcessingWorker failed for task '%s'", self.task)
            self.error.emit(str(e))

    def cancel(self) -> None:
        """Request cancellation of the current task."""
        self._cancelled = True

    # ------------------------------------------------------------------

    def _run_clean(self) -> None:
        from text_processor import TextProcessor

        processor = TextProcessor()
        processor.lm_client.is_cancelled = lambda: self._cancelled

        def on_progress(pct: int, msg: str) -> None:
            if not self._cancelled:
                self.progress.emit(pct, msg)

        result = processor.process(self.text, use_ai=True, on_progress=on_progress)

        if not self._cancelled:
            self.finished.emit(result)

    def _run_generate(self) -> None:
        from article_generator import ArticleGenerator, ArticleFormat

        generator = ArticleGenerator()
        generator.lm_client.is_cancelled = lambda: self._cancelled
        format_key = self.kwargs.get('format', 'blog')
        format_enum = ArticleFormat(format_key)

        def on_progress(pct: int, msg: str) -> None:
            if not self._cancelled:
                self.progress.emit(pct, msg)

        article = generator.generate_article(
            self.text, format_enum, on_progress=on_progress
        )

        if not self._cancelled:
            self.finished.emit(article)

    def _run_generate_all(self) -> None:
        from article_generator import ArticleGenerator

        generator = ArticleGenerator()
        generator.lm_client.is_cancelled = lambda: self._cancelled

        def on_progress(pct: int, msg: str) -> None:
            if not self._cancelled:
                self.progress.emit(pct, msg)

        result = generator.generate_all_formats(
            self.text, on_progress=on_progress
        )

        if not self._cancelled:
            self.finished.emit(result)
