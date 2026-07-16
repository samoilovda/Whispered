"""
Whispered UI - AI Processing Panel
Controls for AI-powered text processing and article generation
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QProgressBar, QFrame
)
from PyQt6.QtCore import pyqtSignal, QTimer

from text_processor import TextProcessor
from article_generator import ArticleGenerator, ArticleFormat, ARTICLE_FORMAT_INFO
from lm_studio_manager import LMStudioManager
from ui.theme import set_role


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
        self.dot.setProperty("role", "danger-text")  # disconnected by default
        self.dot.setStyleSheet("font-size: 10px;")
        layout.addWidget(self.dot)

        self.label = QLabel("LM Studio")
        self.label.setProperty("role", "muted")
        self.label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.label)

        layout.addStretch()

    def set_connected(self, connected: bool, model_name: str = None):
        """Update connection status."""
        self._connected = connected
        self._model_name = model_name

        if connected:
            set_role(self.dot, "success-text")
            if model_name:
                # Truncate long model names
                display_name = model_name[:25] + "..." if len(model_name) > 25 else model_name
                self.label.setText(f"LM Studio: {display_name}")
            else:
                self.label.setText("LM Studio: Connected")
            set_role(self.label, "success-text")
        else:
            set_role(self.dot, "danger-text")
            self.label.setText("LM Studio: Offline")
            set_role(self.label, "muted")
        self.dot.setStyleSheet("font-size: 10px;")
        self.label.setStyleSheet("font-size: 11px;")

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
        self._setup_ui()
        self._start_connection_check()
        self._refresh_models()

    def cleanup(self):
        """Stop timers and cleanup resources."""
        if self.check_timer:
            self.check_timer.stop()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setProperty("role", "divider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        # Section header
        header = QLabel("🤖 AI Processing")
        header.setProperty("role", "heading")
        header.setStyleSheet("margin-top: 4px;")
        layout.addWidget(header)

        # Connection status
        self.status_indicator = StatusIndicator()
        layout.addWidget(self.status_indicator)

        # Model selector (only visible when CLI available)
        model_layout = QHBoxLayout()
        model_layout.setSpacing(4)

        model_label = QLabel("Model:")
        model_label.setProperty("role", "muted")
        model_label.setStyleSheet("font-size: 11px;")
        model_layout.addWidget(model_label)

        self.model_combo = QComboBox()
        # Long LM Studio model names must not dictate the panel width;
        # keep the combo compact and let the dropdown show the full name.
        self.model_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.model_combo.setMinimumContentsLength(14)
        self.model_combo.currentIndexChanged.connect(self._on_model_selected)
        model_layout.addWidget(self.model_combo, stretch=1)

        self.model_row = QWidget()
        self.model_row.setLayout(model_layout)
        self.model_row.setVisible(False)  # Hidden until CLI available
        layout.addWidget(self.model_row)

        # Start server button (only visible when offline)
        self.start_server_btn = QPushButton("▶ Start LM Studio Server")
        self.start_server_btn.setProperty("variant", "primary")
        self.start_server_btn.clicked.connect(self._start_server)
        self.start_server_btn.setVisible(False)
        layout.addWidget(self.start_server_btn)

        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(4)
        layout.addWidget(self.progress_bar)

        # Progress label
        self.progress_label = QLabel("")
        self.progress_label.setProperty("role", "muted")
        self.progress_label.setStyleSheet("font-size: 10px;")
        self.progress_label.setVisible(False)
        layout.addWidget(self.progress_label)

        # Clean Text button
        self.clean_btn = QPushButton("✨ Clean Text")
        self.clean_btn.setStyleSheet("text-align: left;")
        self.clean_btn.setToolTip("Remove filler words, fix punctuation, create paragraphs")
        self.clean_btn.clicked.connect(self._on_clean_clicked)
        self.clean_btn.setEnabled(False)
        layout.addWidget(self.clean_btn)

        # Generate Articles section
        articles_layout = QHBoxLayout()
        articles_layout.setSpacing(4)

        self.generate_btn = QPushButton("📝 Generate")
        self.generate_btn.setStyleSheet("text-align: left;")
        self.generate_btn.setToolTip("Generate article in selected format")
        self.generate_btn.clicked.connect(self._on_generate_clicked)
        self.generate_btn.setEnabled(False)
        articles_layout.addWidget(self.generate_btn, stretch=1)

        # Format selector
        self.format_combo = QComboBox()

        # Add format options
        for fmt in ArticleFormat:
            info = ARTICLE_FORMAT_INFO[fmt]
            # Use emoji in text, not as icon (QIcon requires actual icon, not string)
            display_text = f"{info['icon']} {info['name'].split()[1]}"
            self.format_combo.addItem(display_text, fmt.value)

        # Size to the longest entry rather than a hard-coded width, which
        # truncated labels ("📝 Blog" → "📝 Bl") as soon as the theme's
        # font size grew.
        self.format_combo.setFixedWidth(self.format_combo.sizeHint().width())

        articles_layout.addWidget(self.format_combo)
        layout.addLayout(articles_layout)

        # Generate All button
        self.generate_all_btn = QPushButton("📚 Generate All Formats")
        self.generate_all_btn.setStyleSheet("text-align: left;")
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
        """Check LM Studio connection status."""
        if self._processing:
            return  # Don't check while processing

        connected = self._processor.is_available()
        model_name = self._processor.get_model_name() if connected else None
        self.status_indicator.set_connected(connected, model_name)

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
        set_role(self.status_indicator.label, "warning-text")
        self.status_indicator.label.setStyleSheet("font-size: 11px;")

        # Use a timer to avoid blocking UI
        QTimer.singleShot(100, lambda: self._load_model(model_path))

    def _load_model(self, model_path: str):
        """Load a model in background."""
        success = self._manager.load_model(model_path, gpu="auto")

        if success:
            self._check_connection()
        else:
            self.status_indicator.label.setText("Failed to load model")
            set_role(self.status_indicator.label, "danger-text")
            self.status_indicator.label.setStyleSheet("font-size: 11px;")

    def _start_server(self):
        """Start LM Studio server."""
        self.start_server_btn.setEnabled(False)
        self.start_server_btn.setText("Starting...")

        # Use timer to avoid blocking UI
        QTimer.singleShot(100, self._do_start_server)

    def _do_start_server(self):
        """Actually start the server."""
        success = self._manager.start_server(wait=True, timeout=30)

        if success:
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


