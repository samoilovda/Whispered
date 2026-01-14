"""
Whisper Fedora UI - Main Window
Main application window integrating all components
"""

import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QProgressBar, QLabel, QFileDialog, QMessageBox,
    QApplication, QFrame
)
from PyQt6.QtCore import Qt, QSize

from ui.file_selector import FileSelector
from ui.settings_panel import SettingsPanel
from ui.transcript_view import TranscriptView
from transcriber import Transcriber, TranscriptionResult
from exporters import export_result, EXPORT_FORMATS


class MainWindow(QMainWindow):
    """Main application window."""
    
    def __init__(self):
        super().__init__()
        self.transcriber = Transcriber()
        self._current_result: TranscriptionResult | None = None
        self._setup_ui()
        self._connect_signals()
    
    def _setup_ui(self):
        """Set up the main window UI."""
        self.setWindowTitle("Whisper Fedora")
        self.setMinimumSize(1000, 700)
        self.resize(1200, 800)
        
        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(20)
        
        # Header
        header = self._create_header()
        main_layout.addWidget(header)
        
        # Main content area with splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #3a3a3a;
            }
        """)
        
        # Left panel (file selector + settings)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 16, 0)
        left_layout.setSpacing(20)
        
        # File selector
        self.file_selector = FileSelector()
        left_layout.addWidget(self.file_selector)
        
        # Settings panel
        self.settings_panel = SettingsPanel()
        left_layout.addWidget(self.settings_panel, stretch=1)
        
        splitter.addWidget(left_panel)
        
        # Right panel (transcript view)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(16, 0, 0, 0)
        right_layout.setSpacing(12)
        
        self.transcript_view = TranscriptView()
        right_layout.addWidget(self.transcript_view, stretch=1)
        
        splitter.addWidget(right_panel)
        
        # Set splitter proportions (40% left, 60% right)
        splitter.setSizes([400, 600])
        
        main_layout.addWidget(splitter, stretch=1)
        
        # Bottom action bar
        action_bar = self._create_action_bar()
        main_layout.addWidget(action_bar)
    
    def _create_header(self) -> QWidget:
        """Create the header widget."""
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Logo/Title
        title_container = QWidget()
        title_layout = QHBoxLayout(title_container)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(12)
        
        logo = QLabel("🎙")
        logo.setStyleSheet("font-size: 32px;")
        title_layout.addWidget(logo)
        
        title_text = QWidget()
        title_text_layout = QVBoxLayout(title_text)
        title_text_layout.setContentsMargins(0, 0, 0, 0)
        title_text_layout.setSpacing(0)
        
        title = QLabel("Whisper Fedora")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        title_text_layout.addWidget(title)
        
        subtitle = QLabel("Speech-to-Text Transcription")
        subtitle.setStyleSheet("color: #888; font-size: 12px;")
        title_text_layout.addWidget(subtitle)
        
        title_layout.addWidget(title_text)
        layout.addWidget(title_container)
        
        layout.addStretch()
        
        # GPU indicator
        gpu_type, gpu_name = self.transcriber.gpu_type, self.transcriber.gpu_name
        if gpu_type in ('cuda', 'rocm'):
            gpu_badge = QLabel(f"🚀 {gpu_name}")
            gpu_badge.setStyleSheet("""
                background-color: rgba(34, 197, 94, 0.2);
                color: #22c55e;
                padding: 6px 12px;
                border-radius: 16px;
                font-size: 11px;
            """)
        else:
            gpu_badge = QLabel("💻 CPU Mode")
            gpu_badge.setStyleSheet("""
                background-color: rgba(99, 102, 241, 0.2);
                color: #6366f1;
                padding: 6px 12px;
                border-radius: 16px;
                font-size: 11px;
            """)
        layout.addWidget(gpu_badge)
        
        return header
    
    def _create_action_bar(self) -> QWidget:
        """Create the bottom action bar."""
        bar = QWidget()
        bar.setStyleSheet("""
            QWidget {
                background-color: #1a1a1a;
                border-radius: 12px;
            }
        """)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 16, 20, 16)
        
        # Progress section
        progress_section = QWidget()
        progress_layout = QVBoxLayout(progress_section)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(4)
        
        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet("color: #888; font-size: 12px;")
        progress_layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 4px;
                background-color: #2a2a2a;
                height: 6px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #6366f1;
                border-radius: 4px;
            }
        """)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        progress_layout.addWidget(self.progress_bar)
        
        layout.addWidget(progress_section, stretch=1)
        
        layout.addSpacing(24)
        
        # Action buttons
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                border: 1px solid #4a4a4a;
                border-radius: 8px;
                padding: 12px 24px;
                color: #888;
                font-weight: bold;
            }
            QPushButton:hover {
                border-color: #f87171;
                color: #f87171;
            }
        """)
        self.cancel_btn.clicked.connect(self._cancel_transcription)
        layout.addWidget(self.cancel_btn)
        
        self.transcribe_btn = QPushButton("▶  Transcribe")
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
                border: none;
                border-radius: 8px;
                padding: 12px 32px;
                color: white;
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #818cf8;
            }
            QPushButton:pressed {
                background-color: #4f46e5;
            }
            QPushButton:disabled {
                background-color: #3a3a3a;
                color: #666;
            }
        """)
        self.transcribe_btn.clicked.connect(self._start_transcription)
        layout.addWidget(self.transcribe_btn)
        
        return bar
    
    def _connect_signals(self):
        """Connect widget signals."""
        # File selection
        self.file_selector.file_selected.connect(self._on_file_selected)
        
        # Transcript view actions
        self.transcript_view.copy_requested.connect(self._copy_to_clipboard)
        self.transcript_view.export_requested.connect(self._export_result)
    
    def _on_file_selected(self, filepath: str):
        """Handle file selection."""
        self.transcribe_btn.setEnabled(True)
        self.status_label.setText(f"Ready to transcribe: {os.path.basename(filepath)}")
    
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
        
        # Get settings
        model = self.settings_panel.get_model()
        language = self.settings_panel.get_language()
        translate = self.settings_panel.get_translate()
        
        # Start transcription
        self.transcriber.transcribe(
            filepath=filepath,
            model_name=model,
            language=language,
            translate=translate,
            on_progress=self._on_progress,
            on_finished=self._on_finished,
            on_error=self._on_error
        )
    
    def _cancel_transcription(self):
        """Cancel the current transcription."""
        self.transcriber.cancel()
        self._reset_ui()
        self.status_label.setText("Transcription cancelled")
    
    def _on_progress(self, percentage: int, message: str):
        """Handle progress updates."""
        self.progress_bar.setValue(percentage)
        self.status_label.setText(message)
    
    def _on_finished(self, result: TranscriptionResult):
        """Handle transcription completion."""
        self._current_result = result
        self.transcript_view.set_result(result)
        self._reset_ui()
        
        word_count = len(result.full_text.split())
        self.status_label.setText(f"✓ Transcription complete - {word_count} words")
    
    def _on_error(self, error_message: str):
        """Handle transcription error."""
        self._reset_ui()
        self.status_label.setText(f"❌ Error: {error_message}")
        
        QMessageBox.critical(
            self,
            "Transcription Error",
            f"An error occurred during transcription:\n\n{error_message}"
        )
    
    def _reset_ui(self):
        """Reset UI to ready state."""
        # Only enable transcribe button if a file is still selected
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
            self.status_label.setText("📋 Copied to clipboard")
    
    def _export_result(self):
        """Export the transcription result."""
        result = self.transcript_view.get_result()
        if not result:
            return
        
        # Get export format from settings
        format_key = self.settings_panel.get_export_format()
        format_name, _ = EXPORT_FORMATS[format_key]
        
        # Determine file extension
        if format_key == 'txt' or format_key == 'txt_ts':
            ext = 'txt'
        elif format_key == 'srt':
            ext = 'srt'
        elif format_key == 'vtt':
            ext = 'vtt'
        else:
            ext = 'json'
        
        # Get output file path
        default_name = os.path.splitext(
            os.path.basename(self.file_selector.get_file() or "transcript")
        )[0]
        
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            f"Export as {format_name}",
            f"{default_name}.{ext}",
            f"{format_name} (*.{ext});;All Files (*)"
        )
        
        if not filepath:
            return
        
        try:
            export_result(result, filepath, format_key)
            self.status_label.setText(f"💾 Exported to {os.path.basename(filepath)}")
        except Exception as e:
            QMessageBox.critical(
                self,
                "Export Error",
                f"Failed to export file:\n\n{str(e)}"
            )
