"""
Whisper Fedora UI - AI Processing Panel
Controls for AI-powered text processing and article generation
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
    QLabel, QComboBox, QProgressBar, QFrame, QCheckBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QThread
from PyQt6.QtGui import QFont

from config import get_config
from core.i18n import tr
from text_processor import TextProcessor
from article_generator import ArticleGenerator, ArticleFormat, ARTICLE_FORMAT_INFO
from lm_studio_manager import LMStudioManager


# ============================================================================
# Background workers (keep blocking network / CLI work off the UI thread)
# ============================================================================

class _ConnectionWorker(QThread):
    """Probe LM Studio connection + loaded model name without blocking the UI."""
    result = pyqtSignal(bool, str)  # (connected, model_name)

    def __init__(self, processor: TextProcessor) -> None:
        super().__init__()
        self._processor = processor

    def run(self) -> None:
        connected = self._processor.is_available()
        model = self._processor.get_model_name() if connected else ""
        self.result.emit(connected, model or "")


class _ModelLoadWorker(QThread):
    """Load a model into LM Studio via its CLI (can take a while)."""
    done = pyqtSignal(bool)

    def __init__(self, manager: LMStudioManager, model_path: str) -> None:
        super().__init__()
        self._manager = manager
        self._model_path = model_path

    def run(self) -> None:
        self.done.emit(self._manager.load_model(self._model_path, gpu="auto"))


class _ServerStartWorker(QThread):
    """Start the LM Studio local server (blocks until ready or timeout)."""
    done = pyqtSignal(bool)

    def __init__(self, manager: LMStudioManager) -> None:
        super().__init__()
        self._manager = manager

    def run(self) -> None:
        self.done.emit(self._manager.start_server(wait=True, timeout=30))


class StatusIndicator(QWidget):
    """Small status indicator with colored dot and label."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        self._connected = False
        self._model_name = None
    
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #ef4444; font-size: 10px;")  # Red = disconnected
        layout.addWidget(self.dot)
        
        self.label = QLabel("LM Studio")
        self.label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.label)
        
        layout.addStretch()
    
    def set_connected(self, connected: bool, model_name: str = None):
        """Update connection status."""
        self._connected = connected
        self._model_name = model_name
        
        if connected:
            self.dot.setStyleSheet("color: #22c55e; font-size: 10px;")  # Green
            if model_name:
                # Truncate long model names
                display_name = model_name[:25] + "..." if len(model_name) > 25 else model_name
                self.label.setText(f"LM Studio: {display_name}")
                self.label.setStyleSheet("color: #22c55e; font-size: 11px;")
            else:
                self.label.setText(tr("LM Studio: Connected"))
                self.label.setStyleSheet("color: #22c55e; font-size: 11px;")
        else:
            self.dot.setStyleSheet("color: #ef4444; font-size: 10px;")  # Red
            self.label.setText(tr("LM Studio: Offline"))
            self.label.setStyleSheet("color: #888; font-size: 11px;")
    
    @property
    def is_connected(self) -> bool:
        return self._connected


class AIProcessingPanel(QWidget):
    """Panel with AI processing controls."""
    
    # Signals
    clean_requested = pyqtSignal()
    generate_requested = pyqtSignal(str)  # Article format key
    generate_all_requested = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._processor = TextProcessor()
        self._generator = ArticleGenerator()
        self._manager = LMStudioManager()
        self._processing = False
        self._has_transcription = False
        self.check_timer = None  # Initialize timer reference
        self._conn_worker = None  # in-flight connection probe
        self._load_worker = None  # in-flight model load
        self._server_worker = None  # in-flight server start
        self._setup_ui()
        self._start_connection_check()
        self._refresh_models()

    def cleanup(self):
        """Stop timers and cleanup resources."""
        if self.check_timer:
            self.check_timer.stop()
        for worker in (self._conn_worker, self._load_worker, self._server_worker):
            if worker is not None and worker.isRunning():
                worker.quit()
                worker.wait(2000)
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        
        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("background-color: #3a3a3a;")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        
        # Section header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        title_label = QLabel(tr("AI Processor"))
        title_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #e0e0e0;")
        header_layout.addWidget(title_label)
        layout.addLayout(header_layout)
        
        # Connection status
        self.status_indicator = StatusIndicator()
        layout.addWidget(self.status_indicator)
        
        # Model selector (only visible when CLI available)
        model_layout = QHBoxLayout()
        model_layout.setSpacing(4)
        
        model_label = QLabel(tr("Model:"))
        model_label.setStyleSheet("color: #888; font-size: 11px;")
        model_layout.addWidget(model_label)
        
        self.model_combo = QComboBox()
        self.model_combo.setStyleSheet("""
            QComboBox {
                padding: 4px 6px;
                padding-right: 18px;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                background-color: #2a2a2a;
                color: #e0e0e0;
                font-size: 10px;
            }
            QComboBox:hover { border-color: #5a5a5a; }
            QComboBox::drop-down { width: 14px; border: none; }
            QComboBox::down-arrow { border-left: 3px solid transparent; border-right: 3px solid transparent; border-top: 3px solid #888; }
            QComboBox QAbstractItemView { background-color: #2a2a2a; border: 1px solid #3a3a3a; selection-background-color: #6366f1; color: #e0e0e0; }
        """)
        self.model_combo.currentIndexChanged.connect(self._on_model_selected)
        model_layout.addWidget(self.model_combo, stretch=1)
        
        self.model_combo.setToolTip(
            tr("Switch the model loaded in LM Studio. Shown only when the LM Studio "
            "CLI is installed and the server is connected.")
        )

        self.model_row = QWidget()
        self.model_row.setLayout(model_layout)
        self.model_row.setVisible(False)  # Hidden until CLI available
        layout.addWidget(self.model_row)
        
        # Start server button (only visible when offline)
        self.start_server_btn = QPushButton(tr("▶ Start LM Studio Server"))
        self.start_server_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                color: white;
                font-size: 11px;
            }
            QPushButton:hover { background-color: #818cf8; }
            QPushButton:disabled { background-color: #3a3a3a; color: #666; }
        """)
        self.start_server_btn.setToolTip(
            tr("LM Studio is installed but its local API server is not running. "
            "Click to start it so AI cleaning and article generation become available.")
        )
        self.start_server_btn.clicked.connect(self._start_server)
        self.start_server_btn.setVisible(False)
        layout.addWidget(self.start_server_btn)
        
        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar { 
                border: none; 
                border-radius: 3px; 
                background-color: #2a2a2a; 
                height: 4px; 
            }
            QProgressBar::chunk { 
                background-color: #6366f1; 
                border-radius: 3px; 
            }
        """)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        layout.addWidget(self.progress_bar)
        
        # Progress label
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #888; font-size: 10px;")
        self.progress_label.setVisible(False)
        layout.addWidget(self.progress_label)
        
        # Button style - use solid backgrounds to prevent bleed-through
        button_style = """
            QPushButton {
                background-color: #2d2d2d;
                border: 1px solid #404040;
                border-radius: 6px;
                padding: 8px 12px;
                color: #e0e0e0;
                font-size: 12px;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #3a3a3a;
                border-color: #5a5a5a;
            }
            QPushButton:pressed {
                background-color: #4a4a4a;
            }
            QPushButton:disabled {
                background-color: #252525;
                color: #606060;
                border-color: #333333;
            }
        """
        
        # Clean Text button
        self.clean_btn = QPushButton(tr("✨ Clean Text"))
        self.clean_btn.setStyleSheet(button_style)
        self.clean_btn.setToolTip(tr("Remove filler words, fix punctuation, create paragraphs"))
        self.clean_btn.clicked.connect(self._on_clean_clicked)
        self.clean_btn.setEnabled(False)
        layout.addWidget(self.clean_btn)
        
        # Generate Articles section
        articles_layout = QHBoxLayout()
        articles_layout.setSpacing(4)
        
        self.generate_btn = QPushButton(tr("Generate"))
        self.generate_btn.setStyleSheet(button_style)
        self.generate_btn.setToolTip(tr("Generate article in selected format"))
        self.generate_btn.clicked.connect(self._on_generate_clicked)
        self.generate_btn.setEnabled(False)
        articles_layout.addWidget(self.generate_btn, stretch=1)
        
        # Format selector
        self.format_combo = QComboBox()
        self.format_combo.setFixedWidth(90)
        self.format_combo.setStyleSheet("""
            QComboBox {
                padding: 6px 8px;
                padding-right: 20px;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
                background-color: #2a2a2a;
                color: #e0e0e0;
                font-size: 11px;
            }
            QComboBox:hover {
                border-color: #5a5a5a;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 16px;
                border: none;
            }
            QComboBox::down-arrow {
                width: 0;
                height: 0;
                border-left: 3px solid transparent;
                border-right: 3px solid transparent;
                border-top: 3px solid #888;
            }
            QComboBox QAbstractItemView {
                background-color: #2a2a2a;
                border: 1px solid #3a3a3a;
                selection-background-color: #6366f1;
                color: #e0e0e0;
            }
        """)
        
        # Add format options
        for fmt in ArticleFormat:
            info = ARTICLE_FORMAT_INFO[fmt]
            # Use emoji in text, not as icon (QIcon requires actual icon, not string)
            display_text = f"{info['icon']} {info['name'].split()[1]}"
            self.format_combo.addItem(display_text, fmt.value)
        
        articles_layout.addWidget(self.format_combo)
        layout.addLayout(articles_layout)
        
        # Generate All button
        self.generate_all_btn = QPushButton("📚 Generate All Formats")
        self.generate_all_btn.setStyleSheet(button_style)
        self.generate_all_btn.setToolTip("Generate articles in all 5 formats")
        self.generate_all_btn.clicked.connect(self._on_generate_all_clicked)
        self.generate_all_btn.setEnabled(False)
        layout.addWidget(self.generate_all_btn)
        
        layout.addStretch()
    
    def _start_connection_check(self):
        """Start periodic connection check."""
        self._check_connection()
        
        # Check connection every 10 seconds
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self._check_connection)
        self.check_timer.start(10000)
    
    def _check_connection(self):
        """Kick off an async LM Studio connection check (non-blocking)."""
        if self._processing:
            return  # Don't check while processing
        if self._conn_worker is not None and self._conn_worker.isRunning():
            return  # a probe is already in flight

        self._conn_worker = _ConnectionWorker(self._processor)
        self._conn_worker.result.connect(self._on_connection_result)
        self._conn_worker.finished.connect(
            lambda: setattr(self, '_conn_worker', None)
        )
        self._conn_worker.start()

    def _on_connection_result(self, connected: bool, model_name: str):
        """Apply the result of an async connection probe to the UI."""
        self.status_indicator.set_connected(connected, model_name or None)

        # Update UI based on connection and CLI availability
        cli_available = self._manager.is_cli_available()
        self.model_row.setVisible(cli_available and connected)
        self.start_server_btn.setVisible(cli_available and not connected)

        # Update button states
        self._update_button_states()
    
    def _refresh_models(self):
        """Refresh the model dropdown list."""
        if not self._manager.is_cli_available():
            return
        
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        
        models = self._manager.list_downloaded_models()
        current_model = self._manager.get_current_model()
        
        current_index = 0
        for i, model in enumerate(models):
            self.model_combo.addItem(model.display_name, model.path)
            if current_model and model.path in current_model:
                current_index = i
        
        if self.model_combo.count() > 0:
            self.model_combo.setCurrentIndex(current_index)
        
        self.model_combo.blockSignals(False)
    
    def _on_model_selected(self, index: int):
        """Handle model selection change."""
        if index < 0:
            return
        
        model_path = self.model_combo.itemData(index)
        if not model_path:
            return
        
        # Check if this model is already loaded
        current = self._manager.get_current_model()
        if current and model_path in current:
            return  # Already loaded
        
        # Load the selected model
        self.status_indicator.label.setText("Loading model...")
        self.status_indicator.label.setStyleSheet("color: #f59e0b; font-size: 11px;")

        self._load_model(model_path)

    def _load_model(self, model_path: str):
        """Load a model on a background thread (CLI call can be slow)."""
        if self._load_worker is not None and self._load_worker.isRunning():
            return
        self._load_worker = _ModelLoadWorker(self._manager, model_path)
        self._load_worker.done.connect(self._on_model_loaded)
        self._load_worker.finished.connect(
            lambda: setattr(self, '_load_worker', None)
        )
        self._load_worker.start()

    def _on_model_loaded(self, success: bool):
        """Handle completion of an async model load."""
        if success:
            self._check_connection()
        else:
            self.status_indicator.label.setText("Failed to load model")
            self.status_indicator.label.setStyleSheet("color: #ef4444; font-size: 11px;")
    
    def _start_server(self):
        """Start LM Studio server on a background thread."""
        if self._server_worker is not None and self._server_worker.isRunning():
            return
        self.start_server_btn.setEnabled(False)
        self.start_server_btn.setText("Starting...")

        self._server_worker = _ServerStartWorker(self._manager)
        self._server_worker.done.connect(self._on_server_started)
        self._server_worker.finished.connect(
            lambda: setattr(self, '_server_worker', None)
        )
        self._server_worker.start()

    def _on_server_started(self, success: bool):
        """Handle completion of an async server start."""
        if success:
            self.start_server_btn.setText("▶ Start LM Studio Server")
            self._check_connection()
            self._refresh_models()
        else:
            self.start_server_btn.setText("Failed - Retry")
            self.start_server_btn.setEnabled(True)
    
    def _update_button_states(self):
        """Update button enabled states."""
        connected = self.status_indicator.is_connected
        enabled = self._has_transcription and connected and not self._processing
        
        self.clean_btn.setEnabled(enabled)
        self.generate_btn.setEnabled(enabled)
        self.generate_all_btn.setEnabled(enabled)
    
    def set_has_transcription(self, has_transcription: bool):
        """Enable/disable buttons based on transcription availability."""
        self._has_transcription = has_transcription
        self._update_button_states()
    
    def set_processing(self, processing: bool):
        """Set processing state."""
        self._processing = processing
        self.progress_bar.setVisible(processing)
        self.progress_label.setVisible(processing)
        
        if not processing:
            self.progress_bar.setValue(0)
            self.progress_label.setText("")
        
        self._update_button_states()
    
    def update_progress(self, percentage: int, message: str):
        """Update progress display."""
        self.progress_bar.setValue(percentage)
        self.progress_label.setText(message)
    
    def _on_clean_clicked(self):
        """Handle Clean Text button click."""
        self.clean_requested.emit()
    
    def _on_generate_clicked(self):
        """Handle Generate button click."""
        format_key = self.format_combo.currentData()
        self.generate_requested.emit(format_key)
    
    def _on_generate_all_clicked(self):
        """Handle Generate All button click."""
        self.generate_all_requested.emit()
    
    def get_selected_format(self) -> ArticleFormat:
        """Get the currently selected article format."""
        format_key = self.format_combo.currentData()
        return ArticleFormat(format_key)
