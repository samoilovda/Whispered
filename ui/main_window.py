"""
Whispered - Main Window
Main application window with compact header-bar layout and AI processing
"""

import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QProgressBar, QLabel, QFileDialog, QMessageBox,
    QApplication, QComboBox, QCheckBox, QTabWidget
)
from PyQt6.QtCore import Qt, QSize, QThread, pyqtSignal

from ui.file_selector import FileSelector
from ui.transcript_view import TranscriptView
from ui.ai_panel import AIProcessingPanel
from ui.article_view import ArticleView, CleanedTextView
from ui.batch_panel import BatchPanel
from ui.icons import IconLabel, get_icon, IconColors
from transcriber import Transcriber, TranscriptionResult
from exporters import export_result, EXPORT_FORMATS
from utils import WHISPER_MODELS, WHISPER_LANGUAGES, PERFORMANCE_MODES, detect_gpu, get_thread_count
from config import get_config


# ============================================================================
# BACKGROUND WORKER FOR AI PROCESSING
# ============================================================================

class AIProcessingWorker(QThread):
    """Background worker for AI processing tasks."""
    
    progress = pyqtSignal(int, str)  # percentage, message
    finished = pyqtSignal(object)    # result object
    error = pyqtSignal(str)          # error message
    
    def __init__(self, task: str, text: str, **kwargs):
        super().__init__()
        self.task = task
        self.text = text
        self.kwargs = kwargs
        self._cancelled = False
    
    def run(self):
        try:
            if self.task == "clean":
                self._run_clean()
            elif self.task == "generate":
                self._run_generate()
            elif self.task == "generate_all":
                self._run_generate_all()
        except Exception as e:
            self.error.emit(str(e))
    
    def cancel(self):
        self._cancelled = True
    
    def _run_clean(self):
        from text_processor import TextProcessor
        
        processor = TextProcessor()
        
        def on_progress(pct, msg):
            if not self._cancelled:
                self.progress.emit(pct, msg)
        
        result = processor.process(self.text, use_ai=True, on_progress=on_progress)
        
        if not self._cancelled:
            self.finished.emit(result)
    
    def _run_generate(self):
        from article_generator import ArticleGenerator, ArticleFormat
        
        generator = ArticleGenerator()
        format_key = self.kwargs.get('format', 'blog')
        format_enum = ArticleFormat(format_key)
        
        def on_progress(pct, msg):
            if not self._cancelled:
                self.progress.emit(pct, msg)
        
        article = generator.generate_article(self.text, format_enum, on_progress=on_progress)
        
        if not self._cancelled:
            self.finished.emit(article)
    
    def _run_generate_all(self):
        from article_generator import ArticleGenerator
        
        generator = ArticleGenerator()
        
        def on_progress(pct, msg):
            if not self._cancelled:
                self.progress.emit(pct, msg)
        
        result = generator.generate_all_formats(self.text, on_progress=on_progress)
        
        if not self._cancelled:
            self.finished.emit(result)


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
        # Device toggle: True = use GPU (if available), False = force CPU
        self._use_gpu = True
        self._gpu_type, self._gpu_name = self.transcriber.gpu_type, self.transcriber.gpu_name
        self._setup_ui()
        self._connect_signals()
    
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
        
        event.accept()
    
    
    def _create_header_combo(self, items: list, width: int = 150) -> QComboBox:
        """Create a compact combo box for the header bar."""
        combo = QComboBox()
        combo.setFixedWidth(width)
        combo.setStyleSheet("""
            QComboBox {
                padding: 6px 10px;
                padding-right: 25px;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
                background-color: #2a2a2a;
                color: #e0e0e0;
                font-size: 12px;
            }
            QComboBox:hover {
                border-color: #5a5a5a;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 18px;
                border: none;
                background: transparent;
            }
            QComboBox::down-arrow {
                width: 0;
                height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 4px solid #888;
            }
            QComboBox QAbstractItemView {
                background-color: #2a2a2a;
                border: 1px solid #3a3a3a;
                selection-background-color: #6366f1;
                color: #e0e0e0;
                outline: none;
            }
        """)
        for item in items:
            if isinstance(item, tuple):
                combo.addItem(item[1], item[0])
            else:
                combo.addItem(item)
        return combo
    
    def _setup_ui(self):
        """Set up the main window UI with header-bar layout."""
        self.setWindowTitle("Whispered")
        self.setMinimumSize(1000, 650)
        self.resize(1200, 750)
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 16, 20, 20)
        main_layout.setSpacing(16)
        
        # ===== Header Bar with Settings =====
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(16)
        
        # Logo and title
        logo = IconLabel('microphone', IconColors.PRIMARY, 28)
        header_layout.addWidget(logo)
        
        title = QLabel("Whispered")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        header_layout.addWidget(title)
        
        header_layout.addSpacing(20)
        
        # Model selector
        model_label = QLabel("Model:")
        model_label.setStyleSheet("color: #888; font-size: 12px;")
        header_layout.addWidget(model_label)
        
        self.model_combo = self._create_header_combo(WHISPER_MODELS, 180)
        self.model_combo.setCurrentIndex(1)  # Default to 'base'
        header_layout.addWidget(self.model_combo)
        
        header_layout.addSpacing(8)
        
        # Language selector
        lang_label = QLabel("Language:")
        lang_label.setStyleSheet("color: #888; font-size: 12px;")
        header_layout.addWidget(lang_label)
        
        self.language_combo = self._create_header_combo(WHISPER_LANGUAGES, 120)
        header_layout.addWidget(self.language_combo)
        
        # Translate checkbox
        self.translate_checkbox = QCheckBox("→ EN")
        self.translate_checkbox.setStyleSheet("color: #888; font-size: 11px;")
        self.translate_checkbox.setToolTip("Translate to English")
        header_layout.addWidget(self.translate_checkbox)
        
        header_layout.addSpacing(12)
        
        # Performance mode selector
        perf_label = QLabel("Mode:")
        perf_label.setStyleSheet("color: #888; font-size: 12px;")
        header_layout.addWidget(perf_label)
        
        self.perf_combo = self._create_header_combo(
            [(mode[0], mode[1]) for mode in PERFORMANCE_MODES], 145
        )
        self.perf_combo.setCurrentIndex(1)  # Default to 'Balanced'
        self.perf_combo.setToolTip("Energy vs Speed tradeoff\n\n"
            "🔋 Efficiency: Low CPU, saves battery\n"
            "⚡ Balanced: Moderate CPU usage\n"
            "🚀 Performance: Max speed, high CPU")
        header_layout.addWidget(self.perf_combo)
        
        header_layout.addSpacing(8)
        
        # Diarization toggle
        self.diarization_checkbox = QCheckBox("👥 Speakers")
        self.diarization_checkbox.setStyleSheet("color: #888; font-size: 11px;")
        self.diarization_checkbox.setToolTip("Identify different speakers (requires setup)")
        self.diarization_checkbox.setChecked(get_config().diarization_enabled)
        header_layout.addWidget(self.diarization_checkbox)
        
        header_layout.addStretch()
        
        # Clickable device toggle button
        self.device_btn = QPushButton()
        self.device_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.device_btn.setToolTip("Click to toggle between GPU and CPU")
        self.device_btn.setMinimumWidth(130)  # Prevent truncation
        self.device_btn.clicked.connect(self._toggle_device)
        self._update_device_badge()
        header_layout.addWidget(self.device_btn)
        
        main_layout.addWidget(header)
        
        # ===== Main Content Area =====
        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setHandleWidth(1)
        content_splitter.setStyleSheet("QSplitter::handle { background-color: #3a3a3a; }")
        
        # Left: File selector and AI Panel
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 12, 0)
        left_layout.setSpacing(12)
        
        self.file_selector = FileSelector()
        left_layout.addWidget(self.file_selector)
        
        # Export format checkboxes
        export_label = QLabel("Export formats:")
        export_label.setStyleSheet("color: #888; font-size: 12px; font-weight: bold; margin-top: 8px;")
        left_layout.addWidget(export_label)
        
        checkbox_style = """
            QCheckBox { color: #aaa; font-size: 11px; }
            QCheckBox:checked { color: #e0e0e0; }
            QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #4a4a4a; border-radius: 3px; background: #2a2a2a; }
            QCheckBox::indicator:checked { background: #6366f1; border-color: #6366f1; }
        """
        
        self.format_txt = QCheckBox("Plain Text (.txt)")
        self.format_txt.setStyleSheet(checkbox_style)
        self.format_txt.setChecked(True)
        left_layout.addWidget(self.format_txt)
        
        self.format_srt = QCheckBox("SRT (.srt)")
        self.format_srt.setStyleSheet(checkbox_style)
        left_layout.addWidget(self.format_srt)
        
        self.format_vtt = QCheckBox("WebVTT (.vtt)")
        self.format_vtt.setStyleSheet(checkbox_style)
        left_layout.addWidget(self.format_vtt)
        
        self.format_json = QCheckBox("JSON (.json)")
        self.format_json.setStyleSheet(checkbox_style)
        left_layout.addWidget(self.format_json)
        
        # AI Processing Panel
        self.ai_panel = AIProcessingPanel()
        left_layout.addWidget(self.ai_panel)
        
        # Batch Processing Panel
        self.batch_panel = BatchPanel()
        self.batch_panel.start_requested.connect(self._start_batch_processing)
        left_layout.addWidget(self.batch_panel)
        
        left_layout.addStretch()
        
        content_splitter.addWidget(left_panel)
        
        # Right: Tabbed Content View
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 0, 0, 0)
        right_layout.setSpacing(0)
        
        # Create tabbed view for different content types
        self.content_tabs = QTabWidget()
        self.content_tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background-color: transparent;
            }
            QTabBar::tab {
                background-color: #2a2a2a;
                color: #888;
                padding: 10px 20px;
                margin-right: 2px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                font-size: 12px;
            }
            QTabBar::tab:selected {
                background-color: #3a3a3a;
                color: #e0e0e0;
            }
            QTabBar::tab:hover {
                background-color: #333;
            }
        """)
        
        # Tab 1: Raw Transcription
        self.transcript_view = TranscriptView()
        self.content_tabs.addTab(self.transcript_view, "📝 Transcript")
        
        # Tab 2: Cleaned Text
        self.cleaned_view = CleanedTextView()
        self.content_tabs.addTab(self.cleaned_view, "✨ Cleaned")
        
        # Tab 3: Generated Articles
        self.article_view = ArticleView()
        self.content_tabs.addTab(self.article_view, "📚 Articles")
        
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
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        status_layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: none; border-radius: 3px; background-color: #2a2a2a; height: 4px; }
            QProgressBar::chunk { background-color: #6366f1; border-radius: 3px; }
        """)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        status_layout.addWidget(self.progress_bar)
        
        action_layout.addWidget(status_section, stretch=1)
        action_layout.addSpacing(16)
        
        # Cancel button
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setIcon(get_icon('close', IconColors.MUTED, 14))
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setStyleSheet("""
            QPushButton { background: transparent; border: 1px solid #4a4a4a; border-radius: 6px; padding: 8px 16px; color: #888; font-weight: bold; }
            QPushButton:hover { border-color: #f87171; color: #f87171; }
        """)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._cancel_operation)
        action_layout.addWidget(self.cancel_btn)
        
        # Transcribe button
        self.transcribe_btn = QPushButton("Transcribe")
        self.transcribe_btn.setIcon(get_icon('play', IconColors.WHITE, 14))
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setStyleSheet("""
            QPushButton { background-color: #6366f1; border: none; border-radius: 6px; padding: 10px 24px; color: white; font-weight: bold; font-size: 13px; }
            QPushButton:hover { background-color: #818cf8; }
            QPushButton:pressed { background-color: #4f46e5; }
            QPushButton:disabled { background-color: #3a3a3a; color: #666; }
        """)
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
        self.article_view.copy_done.connect(lambda: self.status_label.setText("Copied to clipboard"))
        self.article_view.export_done.connect(lambda msg: self.status_label.setText(msg))
        self.cleaned_view.copy_requested.connect(lambda: self.status_label.setText("Copied to clipboard"))
    
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
    
    def _on_file_selected(self, filepath: str):
        """Handle file selection."""
        self.transcribe_btn.setEnabled(True)
        self.status_label.setText(f"Ready: {os.path.basename(filepath)}")
    
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
        """Handle progress updates."""
        self.progress_bar.setValue(percentage)
        self.status_label.setText(message)
    
    def _on_finished(self, result: TranscriptionResult):
        """Handle transcription completion."""
        self._current_result = result
        self.transcript_view.set_result(result)
        self._reset_ui()
        
        # Enable AI panel now that we have a transcription
        self.ai_panel.set_has_transcription(True)
        
        word_count = len(result.full_text.split())
        self.status_label.setText(f"Complete - {word_count} words")
        
        # Switch to transcript tab
        self.content_tabs.setCurrentIndex(0)
    
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
            self.status_label.setText("Copied to clipboard")
    
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
                    self.status_label.setText(f"Exported: {os.path.basename(filepath)}")
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
                    except:
                        pass
                self.status_label.setText(f"Exported {count} files")
    
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
