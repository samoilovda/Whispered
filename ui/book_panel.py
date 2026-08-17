"""
Whispered - Book Pipeline Panel
UI panel for the "Book" mode: stages, batch folder processing.
"""

from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QCheckBox, QProgressBar, QFrame, QFileDialog, QLineEdit, QMessageBox,
)
from PyQt6.QtCore import pyqtSignal, QTimer

from book_pipeline import BookPipeline, BookResult
from core.book_batch_worker import BookBatchWorker
from core.i18n import tr
from core.logger import get_logger
from core.lm_status_worker import LMStatusWorker
from core.worker_registry import WorkerRegistry
from ui.theme import set_role

logger = get_logger(__name__)


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
    connection_changed = pyqtSignal(bool, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pipeline = BookPipeline()
        self._batch_worker: BookBatchWorker | None = None
        self._checker: LMStatusWorker | None = None  # guard against duplicate checks
        self._registry = WorkerRegistry(parent=self)
        self._conn_timer: QTimer | None = None
        self._has_transcript = False
        self._connected = False
        self._setup_ui()
        # The deterministic gallery (tools/render_ui_gallery.py) must never
        # touch a user's LM Studio installation or depend on its response
        # time — see the identical guard in ui/ai_panel.py.
        if os.environ.get("WHISPERED_UI_GALLERY") != "1":
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
        divider.setProperty("role", "divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        header = QLabel(tr("book_header"))
        header.setProperty("role", "heading")
        header.setStyleSheet("margin-top: 4px;")
        layout.addWidget(header)

        # ----- LM Studio status -----
        status_row = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setProperty("role", "danger-text")
        self.status_dot.setProperty("size", "small")
        self.status_label = QLabel(tr("book_lm_checking"))
        self.status_label.setProperty("role", "muted")
        self.status_label.setProperty("size", "small")
        status_row.addWidget(self.status_dot)
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        layout.addLayout(status_row)
        self.status_dot.setVisible(False)
        self.status_label.setVisible(False)

        # ----- Stage checkboxes -----
        stages_label = QLabel(tr("book_stages_label"))
        stages_label.setProperty("role", "muted")
        stages_label.setProperty("size", "small")
        layout.addWidget(stages_label)

        self.chk_transcribe = QCheckBox(tr("book_stage_transcribe"))
        self.chk_transcribe.setStyleSheet("")
        self.chk_transcribe.setChecked(True)
        self.chk_transcribe.setEnabled(False)  # always on — result comes from transcriber
        layout.addWidget(self.chk_transcribe)

        self.chk_unwrap = QCheckBox(tr("book_stage_unwrap"))
        self.chk_unwrap.setStyleSheet("")
        self.chk_unwrap.setChecked(True)
        layout.addWidget(self.chk_unwrap)

        # Custom prompt row
        custom_row = QHBoxLayout()
        custom_row.setSpacing(6)
        self.chk_custom = QCheckBox(tr("book_stage_custom"))
        self.chk_custom.setStyleSheet("")
        self.chk_custom.toggled.connect(self._on_custom_toggled)
        custom_row.addWidget(self.chk_custom)
        layout.addLayout(custom_row)

        self.custom_prompt_row = QWidget()
        cp_layout = QHBoxLayout(self.custom_prompt_row)
        cp_layout.setContentsMargins(20, 0, 0, 0)
        cp_layout.setSpacing(4)
        self.custom_prompt_edit = QLineEdit()
        self.custom_prompt_edit.setPlaceholderText(tr("book_custom_prompt_placeholder"))
        self.custom_prompt_browse = QPushButton("…")
        self.custom_prompt_browse.setFixedSize(26, 26)
        self.custom_prompt_browse.clicked.connect(self._browse_custom_prompt)
        cp_layout.addWidget(self.custom_prompt_edit, stretch=1)
        cp_layout.addWidget(self.custom_prompt_browse)
        self.custom_prompt_row.setVisible(False)
        layout.addWidget(self.custom_prompt_row)

        # ----- Progress for single file -----
        self.single_progress = QProgressBar()
        self.single_progress.setVisible(False)
        self.single_progress.setTextVisible(False)
        self.single_progress.setFixedHeight(4)
        layout.addWidget(self.single_progress)

        self.single_status = QLabel("")
        self.single_status.setProperty("role", "muted")
        self.single_status.setProperty("size", "small")
        self.single_status.setVisible(False)
        layout.addWidget(self.single_status)

        # ----- Run / Cancel buttons -----
        run_row = QHBoxLayout()
        self.run_btn = QPushButton(tr("book_run"))
        self.run_btn.setProperty("variant", "primary")
        self.run_btn.setEnabled(False)
        self.run_btn.clicked.connect(self._on_run_clicked)
        run_row.addWidget(self.run_btn)

        self.cancel_btn = QPushButton(tr("btn_cancel"))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        run_row.addWidget(self.cancel_btn)
        layout.addLayout(run_row)

        # ===== Batch section =====
        divider2 = QFrame()
        divider2.setFrameShape(QFrame.Shape.HLine)
        divider2.setProperty("role", "divider")
        divider2.setFixedHeight(1)
        layout.addWidget(divider2)

        batch_header = QLabel(tr("book_batch_header"))
        batch_header.setProperty("role", "heading")
        layout.addWidget(batch_header)

        # Folder picker row
        folder_row = QHBoxLayout()
        folder_row.setSpacing(4)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText(tr("book_folder_placeholder"))
        self.folder_edit.textChanged.connect(self._on_folder_changed)
        self.folder_browse = QPushButton("…")
        self.folder_browse.setFixedSize(26, 26)
        self.folder_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.folder_edit, stretch=1)
        folder_row.addWidget(self.folder_browse)
        layout.addLayout(folder_row)

        # File count label
        self.batch_count_label = QLabel(tr("book_files_none_selected"))
        self.batch_count_label.setProperty("role", "dim")
        self.batch_count_label.setProperty("size", "small")
        layout.addWidget(self.batch_count_label)

        # Batch progress
        self.batch_progress = QProgressBar()
        self.batch_progress.setVisible(False)
        self.batch_progress.setTextVisible(False)
        self.batch_progress.setFixedHeight(6)
        layout.addWidget(self.batch_progress)

        self.batch_status_label = QLabel("")
        self.batch_status_label.setProperty("role", "muted")
        self.batch_status_label.setProperty("size", "small")
        self.batch_status_label.setVisible(False)
        layout.addWidget(self.batch_status_label)

        # Batch start / cancel
        batch_btn_row = QHBoxLayout()
        self.batch_start_btn = QPushButton(tr("book_run_all"))
        self.batch_start_btn.setProperty("variant", "primary")
        self.batch_start_btn.setEnabled(False)
        self.batch_start_btn.clicked.connect(self._start_batch)
        batch_btn_row.addWidget(self.batch_start_btn)

        self.batch_cancel_btn = QPushButton(tr("book_stop"))
        self.batch_cancel_btn.setVisible(False)
        self.batch_cancel_btn.clicked.connect(self._cancel_batch)
        batch_btn_row.addWidget(self.batch_cancel_btn)
        layout.addLayout(batch_btn_row)

        # Folder jobs now live in the one shared queue in StatusBar. Keep
        # the worker methods for compatibility with in-flight sessions,
        # but retire this duplicate picker/progress surface.
        for widget in (
            divider2,
            batch_header,
            self.folder_edit,
            self.folder_browse,
            self.batch_count_label,
            self.batch_progress,
            self.batch_status_label,
            self.batch_start_btn,
            self.batch_cancel_btn,
        ):
            widget.setVisible(False)

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
        self.single_progress.setVisible(False)
        self.single_status.setVisible(False)
        self.run_btn.setVisible(not processing)
        self.cancel_btn.setVisible(False)
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
        self._checker = LMStatusWorker(self._pipeline.base_url, parent=self)
        self._checker.status_ready.connect(self._on_connection_result)
        self._checker.finished.connect(lambda: setattr(self, '_checker', None))
        self._registry.register(self._checker, name="lm_status_checker")
        self._checker.start()

    def _on_connection_result(self, connected: bool, _detail: str = "") -> None:
        self._connected = connected
        self.connection_changed.emit(connected, _detail)
        if connected:
            set_role(self.status_dot, "success-text")
            self.status_label.setText(tr("book_lm_connected"))
            set_role(self.status_label, "success-text")
        else:
            set_role(self.status_dot, "danger-text")
            self.status_label.setText(tr("book_lm_unavailable"))
            set_role(self.status_label, "muted")
        self.status_dot.setProperty("size", "small")
        self.status_label.setProperty("size", "small")
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
            self, tr("book_browse_prompt_title"), "",
            "Markdown / Text (*.md *.txt);;All Files (*)"
        )
        if path:
            self.custom_prompt_edit.setText(path)

    def _browse_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("book_browse_folder_title"))
        if folder:
            self.folder_edit.setText(folder)

    def _on_folder_changed(self, _text: str) -> None:
        files = self._batch_files()
        n = len(files)
        if n == 0:
            self.batch_count_label.setText(tr("book_files_none_found"))
        else:
            self.batch_count_label.setText(tr("book_files_found", n=n))
        self._update_batch_btn()

    def _on_run_clicked(self) -> None:
        do_unwrap = self.chk_unwrap.isChecked()
        do_custom = self.chk_custom.isChecked()
        custom_path = self.custom_prompt_edit.text().strip() if do_custom else ""

        if do_custom and not custom_path:
            QMessageBox.warning(self, tr("book_no_prompt_title"),
                                tr("book_no_prompt_message"))
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
            QMessageBox.warning(self, tr("book_no_prompt_title"),
                                tr("book_no_prompt_message"))
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
            self.batch_status_label.setText(tr("book_cancelling"))

    def shutdown(self) -> None:
        """Part of the Shutdownable protocol (ui/shutdownable.py). Used to
        require MainWindow.closeEvent to reach into ``_batch_worker``
        directly; that guard now lives with the rest of this panel's
        state.

        Also stops the periodic LM Studio connection-check timer and
        retires its worker through ``WorkerRegistry``: left running, the
        10 s repeat timer keeps spawning new ``LMStatusWorker`` threads
        after the window starts closing, and a bare ``wait()`` here would
        either block the GUI thread or drop the last reference to a still
        -running QThread — both of which Qt punishes with a hard abort.

        shutdown_all() (rather than a bare non-blocking retire_all())
        still gives it a short bound to actually stop: the connection
        probe is a cheap local socket call that normally finishes almost
        instantly, but if the whole process exits right after closeEvent
        returns, zero wall-clock time would otherwise be all it gets.
        """
        if self._batch_worker:
            self._cancel_batch()
        if self._conn_timer is not None:
            self._conn_timer.stop()
        self._registry.shutdown_all(timeout_ms=1500)

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
        self.batch_status_label.setText(tr("book_batch_done", completed=completed, total=total))
        self._update_batch_btn()
