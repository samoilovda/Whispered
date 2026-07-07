"""
Whispered - Book Batch Worker
QThread that processes a folder of .md transcript files through BookPipeline.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from book_pipeline import BookPipeline
from core.logger import get_logger

logger = get_logger(__name__)


class BookBatchWorker(QThread):
    """
    Process a list of .md files through the book pipeline in a background thread.

    Signals:
        file_started(index, total, filename)
        file_progress(index, percentage, message)
        file_finished(index, result: BookResult)
        file_error(index, error_message)
        batch_finished(completed, total)
    """

    file_started = pyqtSignal(int, int, str)          # index, total, filename
    file_progress = pyqtSignal(int, int, str)          # index, percentage, message
    file_finished = pyqtSignal(int, object)            # index, BookResult
    file_error = pyqtSignal(int, str)                  # index, error
    batch_finished = pyqtSignal(int, int)              # completed, total

    def __init__(
        self,
        file_paths: list[str],
        do_unwrap: bool = True,
        do_custom: bool = False,
        custom_prompt_path: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.file_paths = file_paths
        self.do_unwrap = do_unwrap
        self.do_custom = do_custom
        self.custom_prompt_path = custom_prompt_path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        pipeline = BookPipeline()
        total = len(self.file_paths)
        completed = 0

        for i, path_str in enumerate(self.file_paths):
            if self._cancelled:
                break

            path = Path(path_str)
            self.file_started.emit(i, total, path.name)

            try:
                text = path.read_text(encoding="utf-8")
            except Exception as e:
                self.file_error.emit(i, f"Не удалось прочитать файл: {e}")
                continue

            def on_progress(pct: int, msg: str, _i=i) -> None:
                if not self._cancelled:
                    self.file_progress.emit(_i, pct, msg)

            try:
                result = pipeline.process(
                    transcript_text=text,
                    source_path=path_str,
                    do_unwrap=self.do_unwrap,
                    do_custom=self.do_custom,
                    custom_prompt_path=self.custom_prompt_path,
                    on_progress=on_progress,
                    is_cancelled=lambda: self._cancelled,
                )
                self.file_finished.emit(i, result)
                if result.success:
                    completed += 1
            except Exception as e:
                logger.exception("BookBatchWorker: error processing %s", path_str)
                self.file_error.emit(i, str(e))

        self.batch_finished.emit(completed, total)
