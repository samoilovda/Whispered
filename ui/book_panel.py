"""
Whispered - Book Pipeline Panel
UI panel for the "Book" mode: stages, batch folder processing.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QCheckBox, QProgressBar, QFrame, QFileDialog, QListWidget,
    QListWidgetItem, QAbstractItemView, QLineEdit, QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QColor

from book_pipeline import BookPipeline, BookResult
from core.book_batch_worker import BookBatchWorker
from core.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared styles
# ---------------------------------------------------------------------------

CHECKBOX_STYLE = """
    QCheckBox { color: #ccc; font-size: 12px; spacing: 6px; }
    QCheckBox:checked { color: #e0e0e0; }
    QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #4a4a4a; border-radius: 3px; background: #2a2a2a; }
    QCheckBox::indicator:checked { background: #6366f1; border-color: #6366f1; }
"""

BTN_SECONDARY = """
    QPushButton {
        background-color: #2d2d2d; border: 1px solid #404040;
        border-radius: 6px; padding: 7px 12px; color: #e0e0e0; font-size: 12px;
    }
    QPushButton:hover { background-color: #3a3a3a; border-color: #5a5a5a; }
    QPushButton:disabled { background-color: #1e1e1e; color: #555; border-color: #333; }
"""

BTN_PRIMARY = """
    QPushButton {
        background-color: #6366f1; border: none;
        border-radius: 6px; padding: 9px 20px; color: white;
        font-size: 13px; font-weight: bold;
    }
    QPushButton:hover { background-color: #818cf8; }
    QPushButton:disabled { background-color: #3a3a3a; color: #666; }
"""


# ============================================================================
# Helpers
# ============================================================================

class ConnectionChecker(QThread):
    status_changed = pyqtSignal(bool)

    def __init__(self, pipeline: BookPipeline) -> None:
        super().__init__()
        self._pipeline = pipeline

    def run(self) -> None:
        connected = self._pipeline.is_available()
        self.status_changed.emit(connected)


# ============================================================================
# BOOK PANEL
# ============================================================================

class BookPanel(QWidget):
    """
    Panel for the Book pipeline mode.

    Signals:
        run_single_requested(do_unwrap, do_custom, custom_prompt_path)
            — emitted when user clicks "Run" for the currently open transcript.
        cancel_requested — user wants to stop current processing.
    """

    run_single_requested = pyqtSignal(bool, bool, str)
    cancel_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pipeline = BookPipeline()
        self._batch_worker: BookBatchWorker | None = None
        self._checker: ConnectionChecker | None = None  # guard against duplicate checks
        self._has_transcript = False
        self._connected = False
        self._setup_ui()
        self._start_connection_check()

    # ------------------------------------------------------------------
    # UI Setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        # ----- Header -----
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("background-color: #3a3a3a;")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        header = QLabel("📖 Книжный конвейер")
        header.setStyleSheet("color: #888; font-size: 12px; font-weight: bold; margin-top: 4px;")
        layout.addWidget(header)

        # ----- LM Studio status -----
        status_row = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #ef4444; font-size: 10px;")
        self.status_label = QLabel("LM Studio: проверка…")
        self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        layout.addLayout(status_row)

        # ----- Stage checkboxes -----
        stages_label = QLabel("Этапы:")
        stages_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(stages_label)

        self.chk_transcribe = QCheckBox("Транскрипция (whisper.cpp)")
        self.chk_transcribe.setStyleSheet(CHECKBOX_STYLE)
        self.chk_transcribe.setChecked(True)
        self.chk_transcribe.setEnabled(False)  # always on — result comes from transcriber
        layout.addWidget(self.chk_transcribe)

        self.chk_unwrap = QCheckBox("Расшивка устной речи")
        self.chk_unwrap.setStyleSheet(CHECKBOX_STYLE)
        self.chk_unwrap.setChecked(True)
        layout.addWidget(self.chk_unwrap)

        # Custom prompt row
        custom_row = QHBoxLayout()
        custom_row.setSpacing(6)
        self.chk_custom = QCheckBox("Произвольный промпт из файла")
        self.chk_custom.setStyleSheet(CHECKBOX_STYLE)
        self.chk_custom.toggled.connect(self._on_custom_toggled)
        custom_row.addWidget(self.chk_custom)
        layout.addLayout(custom_row)

        self.custom_prompt_row = QWidget()
        cp_layout = QHBoxLayout(self.custom_prompt_row)
        cp_layout.setContentsMargins(20, 0, 0, 0)
        cp_layout.setSpacing(4)
        self.custom_prompt_edit = QLineEdit()
        self.custom_prompt_edit.setPlaceholderText("путь к файлу промпта (.md / .txt)")
        self.custom_prompt_edit.setStyleSheet(
            "background: #1e1e1e; border: 1px solid #3a3a3a; border-radius: 4px; "
            "color: #ccc; font-size: 11px; padding: 4px 6px;"
        )
        self.custom_prompt_browse = QPushButton("…")
        self.custom_prompt_browse.setFixedSize(26, 26)
        self.custom_prompt_browse.setStyleSheet(BTN_SECONDARY)
        self.custom_prompt_browse.clicked.connect(self._browse_custom_prompt)
        cp_layout.addWidget(self.custom_prompt_edit, stretch=1)
        cp_layout.addWidget(self.custom_prompt_browse)
        self.custom_prompt_row.setVisible(False)
        layout.addWidget(self.custom_prompt_row)

        # ----- Progress for single file -----
        self.single_progress = QProgressBar()
        self.single_progress.setVisible(False)
        self.single_progress.setStyleSheet(
            "QProgressBar { border: none; border-radius: 3px; background: #2a2a2a; height: 4px; }"
            "QProgressBar::chunk { background: #6366f1; border-radius: 3px; }"
        )
        self.single_progress.setTextVisible(False)
        self.single_progress.setFixedHeight(4)
        layout.addWidget(self.single_progress)

        self.single_status = QLabel("")
        self.single_status.setStyleSheet("color: #888; font-size: 10px;")
        self.single_status.setVisible(False)
        layout.addWidget(self.single_status)

        # ----- Run / Cancel buttons -----
        run_row = QHBoxLayout()
        self.run_btn = QPushButton("▶ Запустить")
        self.run_btn.setStyleSheet(BTN_PRIMARY)
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._on_run_clicked)
        run_row.addWidget(self.run_btn)

        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.setStyleSheet(BTN_SECONDARY)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        run_row.addWidget(self.cancel_btn)
        layout.addLayout(run_row)

        # ===== Batch section =====
        divider2 = QFrame()
        divider2.setFrameShape(QFrame.Shape.HLine)
        divider2.setStyleSheet("background-color: #3a3a3a;")
        divider2.setFixedHeight(1)
        layout.addWidget(divider2)

        batch_header = QLabel("📂 Пакетная обработка папки")
        batch_header.setStyleSheet("color: #888; font-size: 12px; font-weight: bold;")
        layout.addWidget(batch_header)

        # Folder picker row
        folder_row = QHBoxLayout()
        folder_row.setSpacing(4)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("папка с .md файлами транскриптов")
        self.folder_edit.setStyleSheet(
            "background: #1e1e1e; border: 1px solid #3a3a3a; border-radius: 4px; "
            "color: #ccc; font-size: 11px; padding: 4px 6px;"
        )
        self.folder_edit.textChanged.connect(self._on_folder_changed)
        self.folder_browse = QPushButton("…")
        self.folder_browse.setFixedSize(26, 26)
        self.folder_browse.setStyleSheet(BTN_SECONDARY)
        self.folder_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.folder_edit, stretch=1)
        folder_row.addWidget(self.folder_browse)
        layout.addLayout(folder_row)

        # File count label
        self.batch_count_label = QLabel("Файлы не выбраны")
        self.batch_count_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self.batch_count_label)

        # Batch progress
        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        self.batch_progress.setStyleSheet(
            "QProgressBar { border: none; border-radius: 3px; background: #2a2a2a; height: 6px; }"
            "QProgressBar::chunk { background: #22c55e; border-radius: 3px; }"
        )
        self.batch_progress.setTextVisible(False)
        self.batch_progress.setFixedHeight(6)
        layout.addWidget(self.batch_progress)

        self.batch_status_label = QLabel("")
        self.batch_status_label.setStyleSheet("color: #888; font-size: 10px;")
        self.batch_status_label.setVisible(False)
        layout.addWidget(self.batch_status_label)

        # Batch start / cancel
        batch_btn_row = QHBoxLayout()
        self.batch_start_btn = QPushButton("▶ Запустить все")
        self.batch_start_btn.setStyleSheet(BTN_PRIMARY)
        self.batch_start_btn.setEnabled(False)
        self.batch_start_btn.clicked.connect(self._start_batch)
        batch_btn_row.addWidget(self.batch_start_btn)

        self.batch_cancel_btn = QPushButton("Остановить")
        self.batch_cancel_btn.setStyleSheet(BTN_SECONDARY)
        self.batch_cancel_btn.setVisible(False)
        self.batch_cancel_btn.clicked.connect(self._cancel_batch)
        batch_btn_row.addWidget(self.batch_cancel_btn)
        layout.addLayout(batch_btn_row)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_has_transcript(self, has_transcript: bool) -> None:
        """Enable / disable the single-file Run button."""
        self._has_transcript = has_transcript
        self._update_run_btn()

    def set_processing(self, processing: bool) -> None:
        """Show / hide progress widgets for single-file processing."""
        self.single_progress.setVisible(processing)
        self.single_status.setVisible(processing)
        self.run_btn.setVisible(not processing)
        self.cancel_btn.setVisible(processing)
        if not processing:
            self.single_progress.setValue(0)
            self.single_status.setText("")

    def update_progress(self, percentage: int, message: str) -> None:
        self.single_progress.setValue(percentage)
        self.single_status.setText(message)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _start_connection_check(self) -> None:
        # Delay the very first check by 1.5 s so the main window finishes
        # rendering and AIProcessingPanel's own startup check can complete
        # first (both would otherwise fire back-to-back on the same LM Studio).
        QTimer.singleShot(1500, self.refresh_connection)
        self._conn_timer = QTimer(self)
        self._conn_timer.timeout.connect(self.refresh_connection)
        self._conn_timer.start(10_000)   # recheck every 10 s

    def refresh_connection(self) -> None:
        """Trigger an async LM Studio connection check (non-blocking)."""
        if self._checker is not None and self._checker.isRunning():
            return  # a check is already in flight, skip
        self._checker = ConnectionChecker(self._pipeline)
        self._checker.status_changed.connect(self._on_connection_result)
        self._checker.finished.connect(lambda: setattr(self, '_checker', None))
        self._checker.start()

    def _on_connection_result(self, connected: bool) -> None:
        self._connected = connected
        if connected:
            self.status_dot.setStyleSheet("color: #22c55e; font-size: 10px;")
            self.status_label.setText("LM Studio: подключено")
            self.status_label.setStyleSheet("color: #22c55e; font-size: 11px;")
        else:
            self.status_dot.setStyleSheet("color: #ef4444; font-size: 10px;")
            self.status_label.setText("LM Studio: не доступен")
            self.status_label.setStyleSheet("color: #888; font-size: 11px;")
        self._update_run_btn()
        self._update_batch_btn()

    def _update_run_btn(self) -> None:
        self.run_btn.setEnabled(self._has_transcript and self._connected)

    def _update_batch_btn(self) -> None:
        has_files = bool(self._batch_files())
        self.batch_start_btn.setEnabled(self._connected and has_files and self._batch_worker is None)

    def _batch_files(self) -> list[str]:
        folder = self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            return []
        return sorted(str(p) for p in Path(folder).glob("*.md"))

    def _on_custom_toggled(self, checked: bool) -> None:
        self.custom_prompt_row.setVisible(checked)

    def _browse_custom_prompt(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Выбрать файл промпта", "",
            "Markdown / Text (*.md *.txt);;All Files (*)"
        )
        if path:
            self.custom_prompt_edit.setText(path)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выбрать папку с транскриптами")
        if folder:
            self.folder_edit.setText(folder)

    def _on_folder_changed(self, _text: str) -> None:
        files = self._batch_files()
        n = len(files)
        if n == 0:
            self.batch_count_label.setText("Файлы не найдены (нет .md в папке)")
        else:
            self.batch_count_label.setText(f"Найдено файлов: {n}")
        self._update_batch_btn()

    def _on_run_clicked(self) -> None:
        do_unwrap = self.chk_unwrap.isChecked()
        do_custom = self.chk_custom.isChecked()
        custom_path = self.custom_prompt_edit.text().strip() if do_custom else ""

        if do_custom and not custom_path:
            QMessageBox.warning(self, "Нет файла промпта",
                                "Укажите путь к файлу пользовательского промпта.")
            return

        self.run_single_requested.emit(do_unwrap, do_custom, custom_path)

    def _on_cancel_clicked(self) -> None:
        self.cancel_requested.emit()

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------

    def _start_batch(self) -> None:
        files = self._batch_files()
        if not files:
            return

        do_unwrap = self.chk_unwrap.isChecked()
        do_custom = self.chk_custom.isChecked()
        custom_path = self.custom_prompt_edit.text().strip() if do_custom else ""

        if do_custom and not custom_path:
            QMessageBox.warning(self, "Нет файла промпта",
                                "Укажите путь к файлу пользовательского промпта.")
            return

        self._batch_worker = BookBatchWorker(
            file_paths=files,
            do_unwrap=do_unwrap,
            do_custom=do_custom,
            custom_prompt_path=custom_path,
        )
        self._batch_worker.file_started.connect(self._on_batch_file_started)
        self._batch_worker.file_progress.connect(self._on_batch_file_progress)
        self._batch_worker.file_finished.connect(self._on_batch_file_finished)
        self._batch_worker.file_error.connect(self._on_batch_file_error)
        self._batch_worker.batch_finished.connect(self._on_batch_done)

        self.batch_start_btn.setEnabled(False)
        self.batch_cancel_btn.setVisible(True)
        self.batch_progress.setVisible(True)
        self.batch_progress.setMaximum(len(files))
        self.batch_progress.setValue(0)
        self.batch_status_label.setVisible(True)

        self._batch_worker.start()

    def _cancel_batch(self) -> None:
        if self._batch_worker:
            self._batch_worker.cancel()
            self.batch_status_label.setText("Отмена…")

    def _on_batch_file_started(self, index: int, total: int, filename: str) -> None:
        self.batch_status_label.setText(f"[{index + 1}/{total}] {filename}")

    def _on_batch_file_progress(self, index: int, pct: int, msg: str) -> None:
        self.batch_status_label.setText(msg)

    def _on_batch_file_finished(self, index: int, result: BookResult) -> None:
        self.batch_progress.setValue(index + 1)

    def _on_batch_file_error(self, index: int, error: str) -> None:
        logger.warning("Batch error on file %d: %s", index, error)

    def _on_batch_done(self, completed: int, total: int) -> None:
        self._batch_worker = None
        self.batch_cancel_btn.setVisible(False)
        self.batch_start_btn.setEnabled(True)
        self.batch_status_label.setText(f"✅ Готово: {completed}/{total} файлов")
        self._update_batch_btn()
