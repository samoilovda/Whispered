"""
Whispered - Main Window
Main application window with compact header-bar layout and AI processing
"""

import os
import time
from pathlib import Path
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QProgressBar, QLabel, QFileDialog, QMessageBox,
    QApplication, QComboBox, QCheckBox, QTabWidget, QScrollArea, QFrame,
    QTextEdit, QLineEdit, QPlainTextEdit, QStackedWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut, QDragEnterEvent, QDropEvent

from ui.toast import show_toast
from ui.sidebar import Sidebar
from ui.library_view import LibraryView
from ui.record_view import RecordView
from ui.file_selector import FileSelector
from ui.transcript_view import TranscriptView
from ui.ai_panel import AIProcessingPanel
from ui.article_view import ArticleView, CleanedTextView
from ui.batch_panel import BatchPanel
from ui.book_panel import BookPanel
from ui.cut_view import CutView
from ui.icons import IconLabel, get_icon, IconColors
from ui.player_widget import PlayerWidget
from ui.recorder_widget import RecorderWidget
from ui.chat_panel import ChatPanel
from ui.insights_panel import InsightsPanel
from ui.youtube_panel import YouTubePanel
from ui.progress_timeline import ProgressTimeline
from transcriber import Transcriber, TranscriptionResult
from exporters import export_result, EXPORT_FORMATS
from utils import WHISPER_MODELS, WHISPER_LANGUAGES, PERFORMANCE_MODES, get_thread_count, is_supported_format
from config import get_config
from core.ai_worker import AIProcessingWorker
from core.logger import get_logger
from core.i18n import tr
from transcriber import _build_initial_prompt
from timeline_export import write_edl
from video_edit import mark_pauses
from video_cut import assemble_draft, VideoCutError

logger = get_logger(__name__)


# ============================================================================
# MAIN WINDOW
# ============================================================================

class MainWindow(QMainWindow):
    """Main application window with header-bar settings layout."""

    def __init__(self):
        super().__init__()
        self.transcriber = Transcriber()
        self._current_result: TranscriptionResult | None = None
        self._cleaned_text: str | None = None
        self._ai_worker: AIProcessingWorker | None = None
        self._source_filepath: str | None = None
        self._use_gpu = True
        self._gpu_type, self._gpu_name = self.transcriber.gpu_type, self.transcriber.gpu_name
        # ETA tracking
        self._transcription_start: float = 0.0
        self._setup_ui()
        self._connect_signals()
        self.setAcceptDrops(True)
        # Apply saved mic device
        cfg = get_config()
        self.recorder_widget.set_device(getattr(cfg, "mic_device_index", None))

    def closeEvent(self, event):
        """Handle window close - cleanup resources."""
        # Stop any running transcription
        if self.transcriber.is_busy():
            self.transcriber.cancel()

        # Stop AI worker if running
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.cancel()
            self._ai_worker.wait()

        # Cleanup AI panel timers
        if hasattr(self, 'ai_panel'):
            self.ai_panel.cleanup()

        # Stop any running YouTube / Insights / Chat workers
        if hasattr(self, 'youtube_panel'):
            self.youtube_panel.clear()
        if hasattr(self, 'insights_panel'):
            self.insights_panel.clear()
        if hasattr(self, 'chat_panel'):
            self.chat_panel.shutdown()

        # Cleanup batch processing
        if hasattr(self, 'batch_panel') and self.batch_panel.processor.is_processing:
            self.batch_panel.cancel_processing()

        # Cleanup book panel batch worker
        if hasattr(self, 'book_panel') and self.book_panel._batch_worker:
            self.book_panel._cancel_batch()

        event.accept()


    def _create_header_combo(self, items: list, width: int = 150) -> QComboBox:
        """Create a compact combo box for the header bar."""
        combo = QComboBox()
        combo.setFixedWidth(width)
        for item in items:
            if isinstance(item, tuple):
                combo.addItem(item[1], item[0])
            else:
                combo.addItem(item)
        return combo

    def _setup_ui(self):
        """Set up the main window UI: a narrow sidebar rail on the left,
        switching between top-level sections (Library / Queue / Recorder)
        shown in a QStackedWidget on the right."""
        self.setWindowTitle("Whispered")
        self.setMinimumSize(900, 550)
        self.resize(1100, 700)

        # Central widget: sidebar + stacked sections
        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QHBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.section_changed.connect(self._on_section_changed)
        self.sidebar.settings_requested.connect(self._open_settings)
        outer_layout.addWidget(self.sidebar)

        self._stack = QStackedWidget()
        outer_layout.addWidget(self._stack, stretch=1)

        # ===== Library section (the main transcription workspace) =====
        library_page = QWidget()
        main_layout = QVBoxLayout(library_page)
        main_layout.setContentsMargins(20, 16, 20, 20)
        main_layout.setSpacing(16)

        # ===== Header Bar with Settings =====
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        # Row 1: Logo, Title, and Device Toggle
        row1_layout = QHBoxLayout()
        row1_layout.setContentsMargins(0, 0, 0, 0)
        row1_layout.setSpacing(16)

        # Logo and title
        logo = IconLabel('microphone', IconColors.PRIMARY, 28)
        row1_layout.addWidget(logo)

        title = QLabel("Whispered")
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 18px;")
        row1_layout.addWidget(title)

        row1_layout.addStretch()

        # Clickable device toggle button
        self.device_btn = QPushButton()
        self.device_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.device_btn.setToolTip(tr("tooltip_device"))
        self.device_btn.setMinimumWidth(130)  # Prevent truncation
        self.device_btn.clicked.connect(self._toggle_device)
        self._update_device_badge()
        row1_layout.addWidget(self.device_btn)

        header_layout.addLayout(row1_layout)

        # Row 2: Settings (Model, Language, Mode, Diarization)
        row2_layout = QHBoxLayout()
        row2_layout.setContentsMargins(0, 0, 0, 0)
        row2_layout.setSpacing(16)

        # Model selector
        model_label = QLabel(tr("label_model"))
        model_label.setProperty("role", "muted")
        row2_layout.addWidget(model_label)

        self.model_combo = self._create_header_combo(WHISPER_MODELS, 180)
        cfg = get_config()
        _model_idx = next(
            (i for i, (k, _) in enumerate(WHISPER_MODELS) if k == cfg.default_model), 6
        )
        self.model_combo.setCurrentIndex(_model_idx)
        row2_layout.addWidget(self.model_combo)

        row2_layout.addSpacing(8)

        # Language selector
        lang_label = QLabel(tr("label_language"))
        lang_label.setProperty("role", "muted")
        row2_layout.addWidget(lang_label)

        self.language_combo = self._create_header_combo(WHISPER_LANGUAGES, 120)
        _lang_idx = next(
            (i for i, (k, _) in enumerate(WHISPER_LANGUAGES) if k == cfg.default_language), 0
        )
        self.language_combo.setCurrentIndex(_lang_idx)
        row2_layout.addWidget(self.language_combo)

        # Translate checkbox
        self.translate_checkbox = QCheckBox("→ EN")
        self.translate_checkbox.setStyleSheet("")
        self.translate_checkbox.setToolTip(tr("tooltip_translate"))
        row2_layout.addWidget(self.translate_checkbox)

        row2_layout.addSpacing(12)

        # Performance mode selector
        perf_label = QLabel(tr("label_mode"))
        perf_label.setProperty("role", "muted")
        row2_layout.addWidget(perf_label)

        self.perf_combo = self._create_header_combo(
            [(mode[0], mode[1]) for mode in PERFORMANCE_MODES], 145
        )
        _perf_idx = next(
            (i for i, (k, *_) in enumerate(PERFORMANCE_MODES) if k == cfg.performance_mode), 1
        )
        self.perf_combo.setCurrentIndex(_perf_idx)
        self.perf_combo.setToolTip(tr("tooltip_performance_mode"))
        row2_layout.addWidget(self.perf_combo)

        row2_layout.addSpacing(8)

        # Diarization toggle
        self.diarization_checkbox = QCheckBox(tr("label_speakers"))
        self.diarization_checkbox.setStyleSheet("")
        self.diarization_checkbox.setToolTip(tr("tooltip_diarization"))
        self.diarization_checkbox.setChecked(get_config().diarization_enabled)
        row2_layout.addWidget(self.diarization_checkbox)

        row2_layout.addStretch()

        # Recorder widget lives on its own sidebar section now; still
        # created here so _connect_signals/_apply_config_defaults and the
        # Ctrl+R shortcut keep a single stable reference.
        self.recorder_widget = RecorderWidget()
        self.recorder_widget.file_ready.connect(self._on_recording_ready)
        self.recorder_widget.error.connect(self._on_recording_error)

        header_layout.addLayout(row2_layout)

        main_layout.addWidget(header)

        # ===== Main Content Area =====
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setHandleWidth(1)

        # Left: File selector and AI Panel (Scrollable)
        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.Shape.NoFrame)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 12, 0)
        left_layout.setSpacing(12)

        self.file_selector = FileSelector()
        left_layout.addWidget(self.file_selector)

        # AI Processing Panel
        self.ai_panel = AIProcessingPanel()
        left_layout.addWidget(self.ai_panel)

        # Batch Processing Panel lives on its own sidebar section (Queue);
        # still created here so signal wiring stays with the rest of setup.
        self.batch_panel = BatchPanel()
        self.batch_panel.start_requested.connect(self._start_batch_processing)

        # Book Pipeline Panel — created here (not mode-gated) so its
        # connection-check timers start with the rest of setup; it's shown
        # as its own tab on the Record view (see content_tabs below).
        self.book_panel = BookPanel()
        self.book_panel.run_single_requested.connect(self._on_book_run)
        self.book_panel.cancel_requested.connect(self._cancel_operation)

        left_layout.addStretch()
        left_scroll.setWidget(left_panel)

        content_splitter.addWidget(left_scroll)

        # Right: Library list (search + past recordings). Opening a record
        # switches to the Record page built below, which hosts the player
        # and result tabs.
        self.library_view = LibraryView()
        self.library_view.open_record.connect(self._open_record_view)
        content_splitter.addWidget(self.library_view)
        content_splitter.setSizes([280, 720])

        main_layout.addWidget(content_splitter, stretch=1)

        # ===== Bottom Action Bar =====
        action_bar = QWidget()
        action_bar.setProperty("role", "card")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(16, 12, 16, 12)

        # Status and progress
        status_section = QWidget()
        status_layout = QVBoxLayout(status_section)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(4)

        self.status_label = QLabel(tr("label_status_ready"))
        self.status_label.setProperty("role", "muted")
        status_layout.addWidget(self.status_label)

        self.progress_timeline = ProgressTimeline()
        self.progress_timeline.stages = [
            tr("timeline_select"), tr("timeline_extract"), tr("timeline_transcribe"),
            tr("timeline_diarize"), tr("timeline_clean"), tr("timeline_generate"),
        ]
        self.progress_timeline.setVisible(False)
        status_layout.addWidget(self.progress_timeline)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        status_layout.addWidget(self.progress_bar)

        action_layout.addWidget(status_section, stretch=1)
        action_layout.addSpacing(16)

        # Cancel button
        self.cancel_btn = QPushButton(tr("btn_cancel"))
        self.cancel_btn.setIcon(get_icon('close', IconColors.MUTED, 14))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setProperty("variant", "danger")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._cancel_operation)
        action_layout.addWidget(self.cancel_btn)

        # Transcribe button
        self.transcribe_btn = QPushButton(tr("btn_transcribe"))
        self.transcribe_btn.setIcon(get_icon('play', IconColors.WHITE, 14))
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setProperty("variant", "primary")
        self.transcribe_btn.setToolTip(tr("tooltip_transcribe"))
        self.transcribe_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.transcribe_btn.clicked.connect(self._start_transcription)
        action_layout.addWidget(self.transcribe_btn)

        main_layout.addWidget(action_bar)

        self._stack.addWidget(library_page)  # index 0

        # ===== Queue section (batch folder processing) =====
        queue_page = QWidget()
        queue_layout = QVBoxLayout(queue_page)
        queue_layout.setContentsMargins(20, 16, 20, 20)
        queue_layout.addWidget(self.batch_panel)
        self._stack.addWidget(queue_page)  # index 1

        # ===== Recorder section =====
        # RecorderWidget itself is a compact horizontal row (button + timer
        # + level meter) designed for the header bar; center it on its own
        # page rather than resizing its internals for this one placement.
        recorder_page = QWidget()
        recorder_outer = QVBoxLayout(recorder_page)
        recorder_outer.setContentsMargins(20, 16, 20, 20)
        recorder_outer.addStretch()
        recorder_row = QHBoxLayout()
        recorder_row.addStretch()
        recorder_row.addWidget(self.recorder_widget)
        recorder_row.addStretch()
        recorder_outer.addLayout(recorder_row)
        recorder_outer.addStretch()
        self._stack.addWidget(recorder_page)  # index 2

        # ===== Record section (not a sidebar destination — opened by
        # clicking a Library entry or finishing a fresh transcription) =====
        self.record_view = RecordView()
        self.record_view.back_requested.connect(self._show_library)
        self.record_view.export_requested.connect(self._export_result)

        # Audio player (hidden when multimedia backend unavailable)
        self.player = PlayerWidget()

        # Tabbed result view
        self.content_tabs = QTabWidget()

        self.transcript_view = TranscriptView()
        self.content_tabs.addTab(self.transcript_view, tr("tab_transcript"))

        self.cleaned_view = CleanedTextView()
        self.content_tabs.addTab(self.cleaned_view, tr("tab_cleaned"))

        self.article_view = ArticleView()
        self.content_tabs.addTab(self.article_view, tr("tab_articles"))

        self.chat_panel = ChatPanel()
        self.content_tabs.addTab(self.chat_panel, tr("tab_chat"))

        self.insights_panel = InsightsPanel()
        self.content_tabs.addTab(self.insights_panel, tr("tab_insights"))

        self.cut_view = CutView()
        self.cut_view.video_panel.export_edl_requested.connect(self._export_edl)
        self.cut_view.video_panel.mark_pauses_requested.connect(self._mark_pauses)
        self.cut_view.video_panel.assemble_requested.connect(self._assemble_draft)
        self.content_tabs.addTab(self.cut_view, tr("tab_cut"))

        self.youtube_panel = YouTubePanel()
        self.content_tabs.addTab(self.youtube_panel, tr("tab_youtube"))

        self.content_tabs.addTab(self.book_panel, tr("tab_book"))

        self.record_view.add_content_widgets(self.player, self.content_tabs)
        self._record_index = self._stack.addWidget(self.record_view)  # index 3

        self._section_index = {"library": 0, "queue": 1, "recorder": 2}

    def _on_section_changed(self, key: str) -> None:
        """Switch the visible page when a sidebar button is clicked."""
        idx = self._section_index.get(key)
        if idx is not None:
            self._stack.setCurrentIndex(idx)

    def _show_library(self) -> None:
        """Navigate back to the Library page (from the Record view) and
        refresh the list in case anything changed while a record was open."""
        self.sidebar.set_active("library")
        self._stack.setCurrentIndex(self._section_index["library"])
        self.library_view.refresh()

    def _open_record_view(self, record_id: int) -> None:
        """Load a history record and switch to the Record page."""
        self._load_from_history(record_id)
        self._stack.setCurrentIndex(self._record_index)

    def _connect_signals(self):
        """Connect widget signals."""
        self.file_selector.file_selected.connect(self._on_file_selected)
        self.transcript_view.copy_requested.connect(self._copy_to_clipboard)
        self.transcript_view.export_requested.connect(self._export_result)

        # AI Panel signals
        self.ai_panel.clean_requested.connect(self._start_text_cleaning)
        self.ai_panel.generate_requested.connect(self._start_article_generation)
        self.ai_panel.generate_all_requested.connect(self._start_generate_all)

        # Article view signals
        self.article_view.copy_done.connect(lambda: show_toast(self, tr("toast_copied"), kind="success"))
        self.article_view.export_done.connect(lambda msg: show_toast(self, msg, kind="success"))
        self.cleaned_view.copy_requested.connect(lambda: show_toast(self, tr("toast_copied"), kind="success"))

        # Player ↔ transcript sync
        self.player.position_changed_sec.connect(self._on_player_position)
        self.transcript_view.seek_requested.connect(self.player.seek_to)
        self.cut_view.seek_requested.connect(self.player.seek_to)

        # Insights panel
        self.insights_panel.seek_requested.connect(self.player.seek_to)

        # Auto-save each completed batch item to history
        self.batch_panel.processor.item_finished.connect(self._on_batch_item_finished)

        # ── Keyboard shortcuts ────────────────────────────────────
        def _sc(seq, slot):
            s = QShortcut(QKeySequence(seq), self)
            s.activated.connect(slot)

        _sc("Ctrl+,",       self._open_settings)
        _sc("Ctrl+O",       self.file_selector.browse_btn.click)
        _sc("Ctrl+T",       self._start_transcription)
        _sc("Ctrl+E",       self._export_result)
        _sc("Ctrl+Shift+C", self._copy_to_clipboard)
        _sc("Ctrl+R",       self.recorder_widget._toggle_recording)
        # Space: play/pause only when focus is not inside a text input
        _sc("Space",        self._space_play_pause)

    def _toggle_device(self):
        """Toggle between GPU and CPU mode."""
        if self._gpu_type == 'cpu':
            # No GPU available, can't toggle
            self.status_label.setText(tr("status_no_gpu"))
            return

        self._use_gpu = not self._use_gpu
        self._update_device_badge()

        device_name = self._gpu_name if self._use_gpu else "CPU"
        self.status_label.setText(tr("status_switched_device", device=device_name))

    def _update_device_badge(self):
        """Update the device button appearance based on current selection."""
        if self._use_gpu and self._gpu_type in ('cuda', 'rocm'):
            self.device_btn.setText(f"🚀 {self._gpu_name}")
            role = "success-badge"
        elif self._use_gpu and self._gpu_type == 'metal':
            self.device_btn.setText(f"🍎 {self._gpu_name}")
            role = "accent-badge"
        else:
            # CPU mode or no GPU
            self.device_btn.setText("💻 CPU")
            role = "muted-badge"

        self.device_btn.setProperty("role", role)
        # Property-based selectors need an explicit re-polish to take effect
        # once the widget has already been shown with a different role.
        self.device_btn.style().unpolish(self.device_btn)
        self.device_btn.style().polish(self.device_btn)

    def _open_settings(self):
        """Open the Settings dialog and apply any changes to the live UI."""
        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        dlg.exec()
        # Settings only persist on OK/Apply; re-seeding is a no-op on Cancel.
        self._apply_config_defaults()
        self.transcript_view.apply_display_settings()
        # Update recorder device from config
        cfg = get_config()
        self.recorder_widget.set_device(getattr(cfg, "mic_device_index", None))

    def _apply_config_defaults(self):
        """Re-seed header controls from the saved config (after settings change)."""
        cfg = get_config()
        idx = next((i for i, (k, _) in enumerate(WHISPER_MODELS)
                    if k == cfg.default_model), None)
        if idx is not None:
            self.model_combo.setCurrentIndex(idx)
        idx = next((i for i, (k, _) in enumerate(WHISPER_LANGUAGES)
                    if k == cfg.default_language), None)
        if idx is not None:
            self.language_combo.setCurrentIndex(idx)
        idx = next((i for i, (k, *_) in enumerate(PERFORMANCE_MODES)
                    if k == cfg.performance_mode), None)
        if idx is not None:
            self.perf_combo.setCurrentIndex(idx)
        self.diarization_checkbox.setChecked(cfg.diarization_enabled)

    # ------------------------------------------------------------------ drag & drop

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile() and is_supported_format(urls[0].toLocalFile()):
                event.acceptProposedAction()
                self._show_drop_overlay(True)
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._show_drop_overlay(False)

    def dropEvent(self, event: QDropEvent):
        self._show_drop_overlay(False)
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0]
            if url.isLocalFile():
                filepath = url.toLocalFile()
                if is_supported_format(filepath):
                    self.file_selector._set_file(filepath)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def _show_drop_overlay(self, visible: bool):
        if not hasattr(self, "_drop_overlay"):
            overlay = QLabel(tr("drop_overlay"), self)
            overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            overlay.setProperty("role", "drop-overlay")
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._drop_overlay = overlay
        ov = self._drop_overlay
        if visible:
            ov.setGeometry(self.centralWidget().geometry())
            ov.raise_()
            ov.show()
        else:
            ov.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_drop_overlay") and self._drop_overlay.isVisible():
            self._drop_overlay.setGeometry(self.centralWidget().geometry())

    # ------------------------------------------------------------------ helpers

    def _space_play_pause(self):
        """Space play/pause — only fires when focus is not in a text field."""
        focused = QApplication.focusWidget()
        if isinstance(focused, (QTextEdit, QLineEdit, QPlainTextEdit)):
            return
        self.player.toggle_play()

    def _on_player_position(self, seconds: float):
        """Forward player position to transcript highlight (throttled by player timer)."""
        self.transcript_view.highlight_at(seconds)

    def _on_recording_ready(self, filepath: str):
        """Load a freshly-recorded WAV into the file selector."""
        self.file_selector._set_file(filepath)

    def _on_recording_error(self, msg: str):
        QMessageBox.warning(self, tr("error_transcription"), msg)

    def _on_file_selected(self, filepath: str):
        """Handle file selection."""
        self._source_filepath = filepath
        self.transcribe_btn.setEnabled(True)
        self.status_label.setText(tr("status_ready_file", name=os.path.basename(filepath)))
        self.player.load(filepath)

    def _start_transcription(self):
        """Start the transcription process."""
        filepath = self.file_selector.get_file()
        if not filepath:
            return

        # Update UI for transcription mode
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setVisible(False)
        self.cancel_btn.setVisible(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        # Select is done (file chosen); Extract is the first active stage
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(1, 0)
        self.transcript_view.clear()
        self.cleaned_view.clear()
        self.article_view.clear()
        self.chat_panel.clear_transcript()
        self.insights_panel.clear()
        self.youtube_panel.clear()
        self.cut_view.clear()
        self._cleaned_text = None

        # Disable AI panel during transcription
        self.ai_panel.set_has_transcription(False)

        # Get settings from header controls
        model = self.model_combo.currentData()
        language = self.language_combo.currentData()
        translate = self.translate_checkbox.isChecked()
        perf_mode = self.perf_combo.currentData()

        # Determine thread count based on performance mode
        n_threads = get_thread_count(perf_mode)

        enable_diarization = self.diarization_checkbox.isChecked()

        self._transcription_start = time.monotonic()
        # Build initial prompt from custom vocabulary
        vocab = getattr(get_config(), "custom_vocabulary", []) or []
        prompt = _build_initial_prompt(vocab) if vocab else None
        # Start transcription
        self.transcriber.transcribe(
            filepath=filepath,
            model_name=model,
            language=language,
            translate=translate,
            n_threads=n_threads,
            enable_diarization=enable_diarization,
            num_speakers=None,  # Auto-detect
            initial_prompt=prompt,
            word_timestamps=True,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
            on_error=self._on_error
        )

    def _cancel_operation(self):
        """Cancel the current operation (transcription or AI processing)."""
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.cancel()
            self._ai_worker.wait()
            self._ai_worker = None
            self.ai_panel.set_processing(False)
            self.status_label.setText(tr("status_ai_cancelled"))
        else:
            self.transcriber.cancel()
            self.status_label.setText(tr("status_cancelled"))

        self._reset_ui()

    def _start_batch_processing(self):
        """Start batch processing with current settings."""
        model = self.model_combo.currentData()
        language = self.language_combo.currentData()
        translate = self.translate_checkbox.isChecked()
        perf_mode = self.perf_combo.currentData()
        n_threads = get_thread_count(perf_mode)
        enable_diarization = self.diarization_checkbox.isChecked()

        self.status_label.setText(tr("status_batch_starting"))

        self.batch_panel.start_processing(
            model_name=model,
            language=language,
            translate=translate,
            n_threads=n_threads,
            enable_diarization=enable_diarization,
            num_speakers=None
        )

    def _on_progress(self, percentage: int, message: str):
        """Handle progress updates with ETA calculation."""
        self.progress_bar.setValue(percentage)
        self._update_timeline(percentage, message)
        if percentage > 5 and self._transcription_start > 0:
            elapsed = time.monotonic() - self._transcription_start
            eta_sec = (elapsed / percentage) * (100 - percentage)
            eta_min, eta_s = divmod(int(eta_sec), 60)
            if eta_min:
                eta_str = f"~{eta_min}m {eta_s}s left"
            else:
                eta_str = f"~{eta_s}s left"
            self.status_label.setText(f"{message}  ·  {eta_str}")
        else:
            self.status_label.setText(message)

    def _update_timeline(self, percentage: int, message: str):
        """Map a transcription progress update to a timeline stage + local fill.

        Stage indices: 1=Extract, 2=Transcribe, 3=Diarize. The transcriber's
        global percentages are rescaled to a 0-100 fill within the active stage.
        """
        m = message.lower()
        if "convert" in m or "extract" in m:
            self.progress_timeline.set_stage(1, min(100, percentage * 10))  # ~5% global
        elif "speaker" in m or "diariz" in m:
            local = max(0, min(100, int((percentage - 85) / 10 * 100)))
            self.progress_timeline.set_stage(3, local)
        else:
            # Loading / preparing / transcribing / processing results: 10-90% global
            local = max(0, min(100, int((percentage - 10) / 80 * 100)))
            self.progress_timeline.set_stage(2, local)

    def _on_finished(self, result: TranscriptionResult):
        """Handle transcription completion."""
        self._current_result = result
        self.transcript_view.set_result(result)
        self._reset_ui()

        # Enable AI panel (posts mode)
        self.ai_panel.set_has_transcription(True)
        # Enable book panel (book mode)
        self.book_panel.set_has_transcript(True)

        elapsed = time.monotonic() - self._transcription_start if self._transcription_start else 0
        word_count = len(result.full_text.split())
        self.status_label.setText(tr("status_complete", words=word_count, seconds=int(elapsed)))
        show_toast(self, tr("toast_complete", words=word_count), kind="success")

        # Auto-save to history
        self._save_to_history(
            result,
            source_path=self._source_filepath or "",
            model=self.model_combo.currentData() or "",
            speaker_names=getattr(self.transcript_view, "_speaker_names", {}),
        )

        # Feed transcript into chat and insights panels
        self.chat_panel.set_transcript(result.full_text)
        self.insights_panel.set_segments(result.segments, transcript_language=result.language)
        self.youtube_panel.set_segments(result.segments, transcript_language=result.language)
        if self._source_filepath:
            self.youtube_panel.set_source_name(Path(self._source_filepath).stem)

        # Populate the Cut tab's segment list and video actions
        self.cut_view.video_panel.set_has_transcript(True)
        self.cut_view.set_result(result)
        self.content_tabs.setCurrentIndex(0)

        # A fresh transcription result is a record too — open the Record
        # view so the user immediately sees what they just produced.
        title = Path(self._source_filepath).stem if self._source_filepath else tr("app_title")
        self.record_view.set_title(title)
        self.sidebar.set_active("library")
        self._stack.setCurrentIndex(self._record_index)

    def _save_to_history(self, result: TranscriptionResult, source_path: str,
                         model: str, speaker_names: dict):
        """Persist a result to history (if enabled)."""
        cfg = get_config()
        if not getattr(cfg, "history_enabled", True):
            return
        try:
            from core.history import get_history_store
            get_history_store().add(
                result,
                source_path=source_path,
                model=model,
                speaker_names=speaker_names or {},
            )
            self.library_view.refresh()
        except Exception as e:
            logger.warning("Failed to save history: %s", e)

    def _on_batch_item_finished(self, index: int, result):
        """Persist each completed batch item to history."""
        if result is None:
            return
        items = self.batch_panel.processor.items
        source_path = items[index].filepath if 0 <= index < len(items) else ""
        self._save_to_history(
            result,
            source_path=source_path,
            model=self.model_combo.currentData() or "",
            speaker_names=getattr(result, "speaker_names", {}) or {},
        )

    def _load_from_history(self, record_id: int):
        """Restore a TranscriptionResult from a history record."""
        try:
            from core.history import get_history_store
            from transcriber import TranscriptionResult, Segment
            store = get_history_store()
            payload = store.get(record_id)
            if payload is None:
                return
            source_name = store.get_source_name(record_id) or ""

            segments = [
                Segment(
                    start=s["start"],
                    end=s["end"],
                    text=s["text"],
                    speaker=s.get("speaker"),
                )
                for s in payload.get("segments", [])
            ]
            result = TranscriptionResult(
                segments=segments,
                language=payload.get("language", ""),
                duration=payload.get("duration", 0.0),
                speaker_names=payload.get("speaker_names") or {},
            )
            self._current_result = result
            # set_result honours result.speaker_names, restoring renames.
            self.transcript_view.set_result(result)

            self.ai_panel.set_has_transcription(True)
            self.book_panel.set_has_transcript(True)
            self.chat_panel.set_transcript(result.full_text)
            self.insights_panel.set_segments(result.segments, transcript_language=result.language)
            self.youtube_panel.set_segments(result.segments, transcript_language=result.language)
            self.youtube_panel.set_source_name(Path(source_name).stem if source_name else "")
            self.cut_view.video_panel.set_has_transcript(True)
            self.cut_view.set_result(result)
            word_count = len(result.full_text.split())
            self.status_label.setText(tr("toast_loaded_history", words=word_count))
            self.content_tabs.setCurrentIndex(0)
            self.record_view.set_title(Path(source_name).stem if source_name else tr("app_title"))
        except Exception as e:
            logger.warning("Failed to load history record %d: %s", record_id, e)

    def _on_error(self, error_message: str):
        """Handle transcription error."""
        self._reset_ui()
        self.status_label.setText(f"Error: {error_message[:50]}...")

        QMessageBox.critical(
            self,
            tr("error_transcription"),
            tr("error_occurred", detail=error_message),
        )

    def _reset_ui(self):
        """Reset UI to ready state."""
        self.transcribe_btn.setEnabled(self.file_selector.get_file() is not None)
        self.transcribe_btn.setVisible(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
        self.progress_timeline.setVisible(False)

    def _copy_to_clipboard(self):
        """Copy transcription to clipboard."""
        text = self.transcript_view.get_text()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            show_toast(self, tr("toast_copied"), kind="success")

    def _export_result(self):
        """Export the transcription result using the formats currently
        checked in the Record view's Export menu."""
        result = self.transcript_view.get_result()
        if not result:
            return

        format_keys = self.record_view.get_export_formats()
        source_file = self.file_selector.get_file() or "transcript"
        default_name = os.path.splitext(os.path.basename(source_file))[0]

        if len(format_keys) == 1:
            # Single format
            format_key = format_keys[0]
            format_name, _ = EXPORT_FORMATS[format_key]
            ext = 'txt' if format_key in ('txt', 'txt_ts') else format_key
            filepath, _ = QFileDialog.getSaveFileName(
                self, f"Export as {format_name}", f"{default_name}.{ext}",
                f"{format_name} (*.{ext});;All Files (*)"
            )

            if filepath:
                try:
                    export_result(result, filepath, format_key)
                    show_toast(self, tr("toast_exported_one", name=os.path.basename(filepath)), kind="success")
                except Exception as e:
                    QMessageBox.critical(self, tr("error_export"), str(e))
        else:
            # Multiple formats - directory
            directory = QFileDialog.getExistingDirectory(self, "Select Export Directory")
            if directory:
                count = 0
                for format_key in format_keys:
                    ext = 'txt' if format_key in ('txt', 'txt_ts') else format_key
                    suffix = '_ts' if format_key == 'txt_ts' else ''
                    filepath = os.path.join(directory, f"{default_name}{suffix}.{ext}")
                    try:
                        export_result(result, filepath, format_key)
                        count += 1
                    except Exception:
                        pass
                show_toast(self, tr("toast_exported_many", count=count), kind="success")

    # ===== AI Processing Methods =====

    def _get_text_for_ai(self) -> str | None:
        """Get text to use for AI processing (cleaned if available, else raw)."""
        if self._cleaned_text:
            return self._cleaned_text
        if self._current_result:
            return self._current_result.full_text
        return None

    def _start_text_cleaning(self):
        """Start text cleaning with AI."""
        if not self._current_result:
            self.status_label.setText(tr("status_no_transcription_to_clean"))
            return

        self.ai_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(4, 0)   # Clean

        self._ai_worker = AIProcessingWorker("clean", self._current_result.full_text)
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.finished.connect(self._on_clean_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()

    def _start_article_generation(self, format_key: str):
        """Start single article generation."""
        text = self._get_text_for_ai()
        if not text:
            self.status_label.setText(tr("status_no_text_to_process"))
            return

        self.ai_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(5, 0)   # Generate

        self._ai_worker = AIProcessingWorker("generate", text, format=format_key)
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.finished.connect(self._on_generate_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()

    def _start_generate_all(self):
        """Start generation of all article formats."""
        text = self._get_text_for_ai()
        if not text:
            self.status_label.setText(tr("status_no_text_to_process"))
            return

        self.ai_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        self.progress_timeline.setVisible(True)
        self.progress_timeline.set_stage(5, 0)   # Generate

        self._ai_worker = AIProcessingWorker("generate_all", text)
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.finished.connect(self._on_generate_all_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()

    def _on_ai_progress(self, percentage: int, message: str):
        """Handle AI processing progress."""
        self.ai_panel.update_progress(percentage, message)
        self.progress_timeline.set_progress(percentage)
        self.status_label.setText(message)

    def _on_clean_finished(self, result):
        """Handle text cleaning completion."""
        from text_processor import ProcessingResult

        self.ai_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        if isinstance(result, ProcessingResult):
            self._cleaned_text = result.coherent.text

            self.cleaned_view.set_text(
                result.coherent.text,
                original_length=len(result.original),
                removed_fillers=result.cleaned.removed_fillers,
                paragraphs=len(result.coherent.paragraphs)
            )

            # Switch to cleaned tab
            self.content_tabs.setCurrentIndex(1)

            self.status_label.setText(
                f"Cleaned in {result.processing_time:.1f}s - "
                f"removed {result.cleaned.removed_fillers} fillers"
            )

    def _on_generate_finished(self, result):
        """Handle single article generation completion."""
        from article_generator import Article

        self.ai_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        if isinstance(result, Article):
            self.article_view.set_article(result)

            # Switch to articles tab
            self.content_tabs.setCurrentIndex(2)

            self.status_label.setText(f"Generated: {result.title} ({result.word_count} words)")

    def _on_generate_all_finished(self, result):
        """Handle all articles generation completion."""
        from article_generator import GenerationResult

        self.ai_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        if isinstance(result, GenerationResult):
            self.article_view.set_articles(result.articles)

            # Switch to articles tab
            self.content_tabs.setCurrentIndex(2)

            self.status_label.setText(
                f"Generated {len(result.articles)} articles in {result.generation_time:.1f}s"
            )

    def _on_ai_error(self, error_message: str):
        """Handle AI processing error."""
        self.ai_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        self.status_label.setText(f"AI Error: {error_message[:50]}...")
        QMessageBox.warning(self, "AI Processing Error", f"An error occurred:\n\n{error_message}")

    # ===== Video Pipeline Methods =====

    def _export_edl(self):
        """Export kept segments from the Cut view as a CMX3600 EDL file."""
        segs = self.cut_view.get_kept_segments()
        if not segs:
            self.status_label.setText(tr("status_no_segments"))
            return
        cfg = get_config()
        src = self._source_filepath or "clip"
        clip_name = os.path.basename(src)
        default_name = os.path.splitext(clip_name)[0] + ".edl"
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export EDL", default_name, "EDL (*.edl);;All Files (*)"
        )
        if not filepath:
            return
        try:
            write_edl(
                segs, filepath,
                fps=cfg.video_fps,
                drop_frame=cfg.video_drop_frame,
                title=os.path.splitext(clip_name)[0],
                clip_name=clip_name,
            )
            show_toast(self, tr("toast_edl_exported", name=os.path.basename(filepath)), kind="success")
        except Exception as e:
            QMessageBox.critical(self, tr("error_export"), str(e))

    def _mark_pauses(self, threshold: float):
        """Run algorithmic pause/filler detection and uncheck matched segments."""
        if not self._current_result:
            return
        segs = self._current_result.segments
        indices = mark_pauses(segs, min_duration=threshold)
        self.cut_view.mark_indices(indices)
        logger.info("Mark pauses (threshold=%.2fs): %d segments marked", threshold, len(indices))

    def _assemble_draft(self):
        """Cut and concatenate kept segments into a draft MP4 via ffmpeg."""
        src = getattr(self, "_source_filepath", None)
        if not src:
            return
        segs = self.cut_view.get_kept_segments()
        if not segs:
            self.status_label.setText(tr("status_no_segments"))
            return
        base = os.path.splitext(os.path.basename(src))[0]
        default_name = base + "_draft.mp4"
        out_path, _ = QFileDialog.getSaveFileName(
            self, tr("video_assemble_draft"), default_name, "MP4 (*.mp4);;All Files (*)"
        )
        if not out_path:
            return
        show_toast(self, tr("toast_assembling"), kind="info")
        try:
            def _progress(msg: str):
                self.status_label.setText(msg)
            assemble_draft(src, segs, out_path, on_progress=_progress)
            show_toast(self, tr("toast_assembled", name=os.path.basename(out_path)), kind="success")
        except (VideoCutError, Exception) as exc:
            show_toast(self, tr("toast_assemble_error", detail=str(exc)[:80]), kind="error")
            logger.exception("assemble_draft failed: %s", exc)

    # ===== Book Pipeline Methods =====

    def _on_book_run(self, do_unwrap: bool, do_custom: bool, custom_prompt_path: str):
        """Start book pipeline processing for the current transcript."""
        if not self._current_result:
            self.status_label.setText(tr("status_no_transcript_for_book"))
            return

        text = self._current_result.full_text
        source_path = self._source_filepath or "transcript"

        self.book_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)

        self._ai_worker = AIProcessingWorker(
            "book_unwrap", text,
            do_unwrap=do_unwrap,
            do_custom=do_custom,
            custom_prompt_path=custom_prompt_path,
            source_path=source_path,
        )
        self._ai_worker.progress.connect(self._on_book_progress)
        self._ai_worker.finished.connect(self._on_book_finished)
        self._ai_worker.error.connect(self._on_book_error)
        self._ai_worker.start()

    def _on_book_progress(self, percentage: int, message: str):
        """Handle book pipeline progress."""
        self.book_panel.update_progress(percentage, message)
        self.status_label.setText(message)

    def _on_book_finished(self, result):
        """Handle book pipeline completion."""
        from book_pipeline import BookResult

        self.book_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        if isinstance(result, BookResult) and result.stages:
            saved_paths = [s.output_path for s in result.stages if s.success and s.output_path]
            if saved_paths:
                files = ", ".join(os.path.basename(p) for p in saved_paths)
                self.status_label.setText(tr("status_book_saved", files=files))
                # Show result in Cleaned tab
                if result.final_text:
                    self.cleaned_view.set_text(
                        result.final_text,
                        original_length=len(self._current_result.full_text) if self._current_result else 0,
                        removed_fillers=0,
                        paragraphs=result.final_text.count('\n\n') + 1,
                    )
                    self.content_tabs.setCurrentIndex(1)
            else:
                failed = [s.error for s in result.stages if not s.success]
                error = failed[0] if failed else tr("error_unknown")
                self.status_label.setText(tr("status_book_error", error=error))
        else:
            self.status_label.setText(tr("status_book_pipeline_done"))

    def _on_book_error(self, error_message: str):
        """Handle book pipeline error."""
        self.book_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        self.status_label.setText(tr("status_book_error", error=error_message[:60]))
        QMessageBox.warning(self, tr("error_book_pipeline_title"), tr("error_occurred", detail=error_message))

