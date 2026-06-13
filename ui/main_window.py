"""
Whispered - Main Window
Main application window with compact header-bar layout and AI processing
"""

import os
import time
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QProgressBar, QLabel, QFileDialog, QMessageBox,
    QApplication, QComboBox, QCheckBox, QTabWidget, QScrollArea, QFrame,
    QTextEdit, QLineEdit, QPlainTextEdit,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QKeySequence, QShortcut, QDragEnterEvent, QDropEvent

from ui.toast import show_toast
from ui.file_selector import FileSelector
from ui.transcript_view import TranscriptView
from ui.ai_panel import AIProcessingPanel
from ui.article_view import ArticleView, CleanedTextView
from ui.batch_panel import BatchPanel
from ui.book_panel import BookPanel
from ui.history_panel import HistoryPanel
from ui.icons import IconLabel, get_icon, IconColors
from ui.player_widget import PlayerWidget
from transcriber import Transcriber, TranscriptionResult
from exporters import export_result, EXPORT_FORMATS
from utils import WHISPER_MODELS, WHISPER_LANGUAGES, PERFORMANCE_MODES, detect_gpu, get_thread_count, is_supported_format
from config import get_config, save_config
from core.ai_worker import AIProcessingWorker
from core.logger import get_logger

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

        # Cleanup batch processing
        if hasattr(self, 'batch_panel') and self.batch_panel.processor.is_processing:
            self.batch_panel.cancel_processing()

        # Cleanup book panel batch worker
        if hasattr(self, 'book_panel') and self.book_panel._batch_worker:
            self.book_panel._cancel_batch()

        # Save current mode to config
        cfg = get_config()
        cfg.pipeline_mode = 'book' if self.mode_combo.currentData() == 'book' else 'posts'
        save_config()

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
        """Set up the main window UI with header-bar layout."""
        self.setWindowTitle("Whispered")
        self.setMinimumSize(900, 550)
        self.resize(1100, 700)
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
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
        title.setStyleSheet("font-size: 18px; font-weight: bold; background: transparent;")
        row1_layout.addWidget(title)
        
        row1_layout.addStretch()
        
        # Mode switcher: Posts / Book
        mode_label = QLabel("Режим:")
        mode_label.setStyleSheet("color: #888888;")
        row1_layout.addWidget(mode_label)

        self.mode_combo = QComboBox()
        self.mode_combo.setFixedWidth(120)
        self.mode_combo.addItem("📝 Посты", "posts")
        self.mode_combo.addItem("📖 Книга", "book")
        # Restore saved mode
        saved_mode = get_config().pipeline_mode
        self.mode_combo.setCurrentIndex(1 if saved_mode == 'book' else 0)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row1_layout.addWidget(self.mode_combo)

        row1_layout.addSpacing(8)

        # Clickable device toggle button
        self.device_btn = QPushButton()
        self.device_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.device_btn.setToolTip("Click to toggle between GPU and CPU")
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
        model_label = QLabel("Model:")
        model_label.setStyleSheet("color: #888888;")
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
        lang_label = QLabel("Language:")
        lang_label.setStyleSheet("color: #888888;")
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
        self.translate_checkbox.setToolTip("Translate to English")
        row2_layout.addWidget(self.translate_checkbox)
        
        row2_layout.addSpacing(12)
        
        # Performance mode selector
        perf_label = QLabel("Mode:")
        perf_label.setStyleSheet("color: #888888;")
        row2_layout.addWidget(perf_label)
        
        self.perf_combo = self._create_header_combo(
            [(mode[0], mode[1]) for mode in PERFORMANCE_MODES], 145
        )
        _perf_idx = next(
            (i for i, (k, *_) in enumerate(PERFORMANCE_MODES) if k == cfg.performance_mode), 1
        )
        self.perf_combo.setCurrentIndex(_perf_idx)
        self.perf_combo.setToolTip("Energy vs Speed tradeoff\n\n"
            "🔋 Efficiency: Low CPU, saves battery\n"
            "⚡ Balanced: Moderate CPU usage\n"
            "🚀 Performance: Max speed, high CPU")
        row2_layout.addWidget(self.perf_combo)
        
        row2_layout.addSpacing(8)
        
        # Diarization toggle
        self.diarization_checkbox = QCheckBox("👥 Speakers")
        self.diarization_checkbox.setStyleSheet("")
        self.diarization_checkbox.setToolTip("Identify different speakers (requires setup)")
        self.diarization_checkbox.setChecked(get_config().diarization_enabled)
        row2_layout.addWidget(self.diarization_checkbox)
        
        row2_layout.addStretch()

        # Settings button (⚙) — opens the settings dialog
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setToolTip("Settings  (Ctrl+,)")
        self.settings_btn.setFixedSize(28, 28)
        self.settings_btn.setProperty("variant", "ghost")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self._open_settings)
        row2_layout.addWidget(self.settings_btn)
        
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
        
        # Export format checkboxes
        export_label = QLabel("Export formats:")
        export_label.setStyleSheet("font-weight: bold; margin-top: 8px;")
        left_layout.addWidget(export_label)

        self.format_txt = QCheckBox("Plain Text (.txt)")
        self.format_txt.setChecked(True)
        left_layout.addWidget(self.format_txt)

        self.format_srt = QCheckBox("SRT (.srt)")
        left_layout.addWidget(self.format_srt)

        self.format_vtt = QCheckBox("WebVTT (.vtt)")
        left_layout.addWidget(self.format_vtt)

        self.format_json = QCheckBox("JSON (.json)")
        left_layout.addWidget(self.format_json)

        self.format_md = QCheckBox("Markdown (.md)")
        left_layout.addWidget(self.format_md)
        
        # AI Processing Panel
        self.ai_panel = AIProcessingPanel()
        left_layout.addWidget(self.ai_panel)

        # Batch Processing Panel (posts mode)
        self.batch_panel = BatchPanel()
        self.batch_panel.start_requested.connect(self._start_batch_processing)
        left_layout.addWidget(self.batch_panel)

        # Book Pipeline Panel
        self.book_panel = BookPanel()
        self.book_panel.run_single_requested.connect(self._on_book_run)
        self.book_panel.cancel_requested.connect(self._cancel_operation)
        left_layout.addWidget(self.book_panel)

        left_layout.addStretch()
        left_scroll.setWidget(left_panel)
        
        content_splitter.addWidget(left_scroll)
        
        # Right: Tabbed Content View
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(4)

        # Audio player (hidden when multimedia backend unavailable)
        self.player = PlayerWidget()
        right_layout.addWidget(self.player)

        # Create tabbed view for different content types
        self.content_tabs = QTabWidget()

        # Tab 1: Raw Transcription
        self.transcript_view = TranscriptView()
        self.content_tabs.addTab(self.transcript_view, "📝 Transcript")
        
        # Tab 2: Cleaned Text
        self.cleaned_view = CleanedTextView()
        self.content_tabs.addTab(self.cleaned_view, "✨ Cleaned")
        
        # Tab 3: Generated Articles
        self.article_view = ArticleView()
        self.content_tabs.addTab(self.article_view, "📚 Articles")

        # Tab 4: History
        self.history_panel = HistoryPanel()
        self.content_tabs.addTab(self.history_panel, "🕒 History")

        right_layout.addWidget(self.content_tabs)
        
        content_splitter.addWidget(right_panel)
        content_splitter.setSizes([280, 720])
        
        main_layout.addWidget(content_splitter, stretch=1)
        
        # ===== Bottom Action Bar =====
        action_bar = QWidget()
        action_bar.setStyleSheet("background-color: #1a1a1a; border-radius: 10px;")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(16, 12, 16, 12)
        
        # Status and progress
        status_section = QWidget()
        status_layout = QVBoxLayout(status_section)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(4)
        
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #888888;")
        status_layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        status_layout.addWidget(self.progress_bar)
        
        action_layout.addWidget(status_section, stretch=1)
        action_layout.addSpacing(16)
        
        # Cancel button
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setIcon(get_icon('close', IconColors.MUTED, 14))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setProperty("variant", "danger")
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._cancel_operation)
        action_layout.addWidget(self.cancel_btn)
        
        # Transcribe button
        self.transcribe_btn = QPushButton("Transcribe")
        self.transcribe_btn.setIcon(get_icon('play', IconColors.WHITE, 14))
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setProperty("variant", "primary")
        self.transcribe_btn.setToolTip("Transcribe  (Ctrl+T)")
        self.transcribe_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.transcribe_btn.clicked.connect(self._start_transcription)
        action_layout.addWidget(self.transcribe_btn)
        
        main_layout.addWidget(action_bar)
    
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
        self.article_view.copy_done.connect(lambda: show_toast(self, "Copied to clipboard", kind="success"))
        self.article_view.export_done.connect(lambda msg: show_toast(self, msg, kind="success"))
        self.cleaned_view.copy_requested.connect(lambda: show_toast(self, "Copied to clipboard", kind="success"))

        # Apply initial mode visibility
        self._on_mode_changed(self.mode_combo.currentIndex())

        # Player ↔ transcript sync
        self.player.position_changed_sec.connect(self._on_player_position)
        self.transcript_view.seek_requested.connect(self.player.seek_to)

        # History panel
        self.history_panel.open_record.connect(self._load_from_history)

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
        # Space: play/pause only when focus is not inside a text input
        _sc("Space",        self._space_play_pause)
    
    def _toggle_device(self):
        """Toggle between GPU and CPU mode."""
        if self._gpu_type == 'cpu':
            # No GPU available, can't toggle
            self.status_label.setText("No GPU available - CPU only mode")
            return
        
        self._use_gpu = not self._use_gpu
        self._update_device_badge()
        
        device_name = self._gpu_name if self._use_gpu else "CPU"
        self.status_label.setText(f"Switched to: {device_name}")
    
    def _update_device_badge(self):
        """Update the device button appearance based on current selection."""
        if self._use_gpu and self._gpu_type in ('cuda', 'rocm'):
            self.device_btn.setText(f"🚀 {self._gpu_name}")
            self.device_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(34, 197, 94, 0.2);
                    border: none;
                    border-radius: 12px;
                    padding: 4px 12px;
                    color: #22c55e;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: rgba(34, 197, 94, 0.3);
                }
            """)
        elif self._use_gpu and self._gpu_type == 'metal':
            self.device_btn.setText(f"🍎 {self._gpu_name}")
            self.device_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(99, 102, 241, 0.2);
                    border: none;
                    border-radius: 12px;
                    padding: 4px 12px;
                    color: #6366f1;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: rgba(99, 102, 241, 0.3);
                }
            """)
        else:
            # CPU mode or no GPU
            self.device_btn.setText("💻 CPU")
            self.device_btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(136, 136, 136, 0.2);
                    border: none;
                    border-radius: 12px;
                    padding: 4px 12px;
                    color: #888;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: rgba(136, 136, 136, 0.3);
                }
            """)
    
    def _on_mode_changed(self, index: int):
        """Switch between Posts and Book pipeline modes."""
        is_book = self.mode_combo.currentData() == 'book'
        # Posts mode panels
        self.ai_panel.setVisible(not is_book)
        self.batch_panel.setVisible(not is_book)
        # Book mode panel
        self.book_panel.setVisible(is_book)
        # Immediately recheck LM Studio when switching to Book mode
        # (avoids waiting up to 10s for the next timer tick)
        if is_book:
            self.book_panel.refresh_connection()

    def _open_settings(self):
        """Open the Settings dialog and apply any changes to the live UI."""
        from ui.settings_dialog import SettingsDialog
        dlg = SettingsDialog(self)
        dlg.exec()
        # Settings only persist on OK/Apply; re-seeding is a no-op on Cancel.
        self._apply_config_defaults()
        self.transcript_view.apply_display_settings()

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
            overlay = QLabel("Drop file to transcribe", self)
            overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
            overlay.setStyleSheet("""
                QLabel {
                    background-color: rgba(99, 102, 241, 0.18);
                    border: 2px dashed #6366f1;
                    border-radius: 12px;
                    color: #6366f1;
                    font-size: 20px;
                    font-weight: bold;
                }
            """)
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

    def _on_file_selected(self, filepath: str):
        """Handle file selection."""
        self._source_filepath = filepath
        self.transcribe_btn.setEnabled(True)
        self.status_label.setText(f"Ready: {os.path.basename(filepath)}")
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
        self.transcript_view.clear()
        self.cleaned_view.clear()
        self.article_view.clear()
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
        
        # Get diarization settings
        enable_diarization = self.diarization_checkbox.isChecked()
        
        self._transcription_start = time.monotonic()
        # Start transcription
        self.transcriber.transcribe(
            filepath=filepath,
            model_name=model,
            language=language,
            translate=translate,
            n_threads=n_threads,
            enable_diarization=enable_diarization,
            num_speakers=None,  # Auto-detect
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
            self.status_label.setText("AI processing cancelled")
        else:
            self.transcriber.cancel()
            self.status_label.setText("Transcription cancelled")
        
        self._reset_ui()
    
    def _start_batch_processing(self):
        """Start batch processing with current settings."""
        model = self.model_combo.currentData()
        language = self.language_combo.currentData()
        translate = self.translate_checkbox.isChecked()
        perf_mode = self.perf_combo.currentData()
        n_threads = get_thread_count(perf_mode)
        enable_diarization = self.diarization_checkbox.isChecked()
        
        self.status_label.setText("Starting batch processing...")
        
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
        self.status_label.setText(f"Complete — {word_count} words in {elapsed:.0f}s")
        show_toast(self, f"Transcription complete — {word_count} words", kind="success")

        # Auto-save to history
        self._save_to_history(
            result,
            source_path=self._source_filepath or "",
            model=self.model_combo.currentData() or "",
            speaker_names=getattr(self.transcript_view, "_speaker_names", {}),
        )

        # Switch to transcript tab
        self.content_tabs.setCurrentIndex(0)

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
            self.history_panel.refresh()
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
            payload = get_history_store().get(record_id)
            if payload is None:
                return

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
            word_count = len(result.full_text.split())
            self.status_label.setText(f"Loaded from history – {word_count} words")
            self.content_tabs.setCurrentIndex(0)
        except Exception as e:
            logger.warning("Failed to load history record %d: %s", record_id, e)
    
    def _on_error(self, error_message: str):
        """Handle transcription error."""
        self._reset_ui()
        self.status_label.setText(f"Error: {error_message[:50]}...")
        
        QMessageBox.critical(self, "Transcription Error", f"An error occurred:\n\n{error_message}")
    
    def _reset_ui(self):
        """Reset UI to ready state."""
        self.transcribe_btn.setEnabled(self.file_selector.get_file() is not None)
        self.transcribe_btn.setVisible(True)
        self.cancel_btn.setVisible(False)
        self.progress_bar.setVisible(False)
    
    def _copy_to_clipboard(self):
        """Copy transcription to clipboard."""
        text = self.transcript_view.get_text()
        if text:
            clipboard = QApplication.clipboard()
            clipboard.setText(text)
            show_toast(self, "Copied to clipboard", kind="success")
    
    def _get_export_formats(self) -> list[str]:
        """Get list of selected export formats."""
        formats = []
        if self.format_txt.isChecked():
            formats.append('txt')
        if self.format_srt.isChecked():
            formats.append('srt')
        if self.format_vtt.isChecked():
            formats.append('vtt')
        if self.format_json.isChecked():
            formats.append('json')
        if self.format_md.isChecked():
            formats.append('md')
        return formats if formats else ['txt']
    
    def _export_result(self):
        """Export the transcription result."""
        result = self.transcript_view.get_result()
        if not result:
            return
        
        format_keys = self._get_export_formats()
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
                    show_toast(self, f"Exported: {os.path.basename(filepath)}", kind="success")
                except Exception as e:
                    QMessageBox.critical(self, "Export Error", str(e))
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
                show_toast(self, f"Exported {count} files", kind="success")
    
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
            self.status_label.setText("No transcription to clean")
            return
        
        self.ai_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        
        self._ai_worker = AIProcessingWorker("clean", self._current_result.full_text)
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.finished.connect(self._on_clean_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()
    
    def _start_article_generation(self, format_key: str):
        """Start single article generation."""
        text = self._get_text_for_ai()
        if not text:
            self.status_label.setText("No text to process")
            return
        
        self.ai_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        
        self._ai_worker = AIProcessingWorker("generate", text, format=format_key)
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.finished.connect(self._on_generate_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()
    
    def _start_generate_all(self):
        """Start generation of all article formats."""
        text = self._get_text_for_ai()
        if not text:
            self.status_label.setText("No text to process")
            return
        
        self.ai_panel.set_processing(True)
        self.cancel_btn.setVisible(True)
        self.transcribe_btn.setVisible(False)
        
        self._ai_worker = AIProcessingWorker("generate_all", text)
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.finished.connect(self._on_generate_all_finished)
        self._ai_worker.error.connect(self._on_ai_error)
        self._ai_worker.start()
    
    def _on_ai_progress(self, percentage: int, message: str):
        """Handle AI processing progress."""
        self.ai_panel.update_progress(percentage, message)
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

    # ===== Book Pipeline Methods =====

    def _on_book_run(self, do_unwrap: bool, do_custom: bool, custom_prompt_path: str):
        """Start book pipeline processing for the current transcript."""
        if not self._current_result:
            self.status_label.setText("Нет транскрипта для обработки")
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
                self.status_label.setText(f"Сохранено: {', '.join(os.path.basename(p) for p in saved_paths)}")
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
                self.status_label.setText(f"Ошибка: {failed[0] if failed else 'неизвестная ошибка'}")
        else:
            self.status_label.setText("Книжный конвейер завершён")

    def _on_book_error(self, error_message: str):
        """Handle book pipeline error."""
        self.book_panel.set_processing(False)
        self._reset_ui()
        self._ai_worker = None

        self.status_label.setText(f"Ошибка: {error_message[:60]}")
        QMessageBox.warning(self, "Ошибка книжного конвейера", f"Произошла ошибка:\n\n{error_message}")

