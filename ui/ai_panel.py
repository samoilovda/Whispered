"""
Whispered UI - AI Processing Panel
Controls for AI-powered text processing and article generation
"""

import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QProgressBar, QFrame
)
from PyQt6.QtCore import pyqtSignal, QTimer

from text_processor import TextProcessor
from article_generator import ArticleGenerator, ArticleFormat, ARTICLE_FORMAT_INFO
from lm_studio_manager import LMStudioManager
from core.base_worker import BaseWorker
from core.i18n import tr
from core.logger import get_logger
from ui.theme import set_role


logger = get_logger(__name__)


class LMStudioTaskWorker(BaseWorker):
    """Run one LM Studio status/CLI operation outside the GUI thread."""

    result_ready = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(
        self,
        action: str,
        processor: TextProcessor,
        manager: LMStudioManager,
        payload=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.action = action
        self._processor = processor
        self._manager = manager
        self._payload = payload

    def _execute(self) -> None:
        result = self._run_action()
        if not self.is_cancelled():
            self.result_ready.emit(self.action, result)

    def _on_error(self, msg: str) -> None:
        # Matches the pre-BaseWorker behaviour: a failure racing a cancel()
        # is swallowed rather than reported, same as a racing success.
        if not self.is_cancelled():
            self.failed.emit(self.action, msg)

    def _run_action(self):
        if self.action == "status":
            client = self._processor.lm_client
            connected = client.check_connection(timeout=1.5)
            if self.is_cancelled():
                return None
            model_name = client.get_loaded_model(timeout=1.5) if connected else None
            return {
                "connected": connected,
                "model_name": model_name,
                "cli_available": self._manager.is_cli_available(),
            }
        if self.action == "models":
            models = self._manager.list_downloaded_models(
                refresh=True, cancel_event=self._cancelled
            )
            current = self._manager.get_current_model(cancel_event=self._cancelled)
            return {"models": models, "current": current}
        if self.action == "load":
            return self._manager.load_model(
                str(self._payload), gpu="auto", cancel_event=self._cancelled
            )
        if self.action == "start":
            return self._manager.start_server(
                wait=True, timeout=30, cancel_event=self._cancelled
            )
        raise ValueError(f"Unknown LM Studio action: {self.action}")


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
        self.dot.setProperty("size", "small")
        layout.addWidget(self.dot)

        self.label = QLabel("LM Studio")
        self.label.setProperty("role", "muted")
        self.label.setProperty("size", "small")
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
                self.label.setText(tr("ai_lm_connected"))
            set_role(self.label, "success-text")
        else:
            set_role(self.dot, "danger-text")
            self.label.setText(tr("ai_lm_offline"))
            set_role(self.label, "muted")
        self.dot.setProperty("size", "small")
        self.label.setProperty("size", "small")

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
        self._worker: LMStudioTaskWorker | None = None
        self._pending_task: tuple[str, object] | None = None
        self._closing = False
        self._current_model: str | None = None
        self.check_timer = None  # Initialize timer reference
        self._setup_ui()
        # The deterministic gallery must never touch a user's LM Studio
        # installation.  More importantly, only ask the CLI for its model
        # list when the local API is actually reachable: ``lms ls --json``
        # can take up to 30 seconds to time out while the server is offline,
        # and this constructor runs on the GUI thread during app startup.
        if os.environ.get("WHISPERED_UI_GALLERY") != "1":
            self._start_connection_check()

    def shutdown(self) -> None:
        """Stop timers and cleanup resources. Part of the Shutdownable
        protocol (ui/shutdownable.py) — called once from closeEvent."""
        if self.check_timer:
            self.check_timer.stop()
        self._closing = True
        self._pending_task = None
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            if not self._worker.wait(4000):
                logger.warning("LM Studio worker did not stop during cleanup")

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
        header = QLabel(tr("ai_panel_title"))
        header.setProperty("role", "heading")
        header.setStyleSheet("margin-top: 4px;")
        layout.addWidget(header)

        # Connection status
        self.status_indicator = StatusIndicator()
        layout.addWidget(self.status_indicator)

        # Model selector (only visible when CLI available)
        model_layout = QHBoxLayout()
        model_layout.setSpacing(4)

        model_label = QLabel(tr("label_model"))
        model_label.setProperty("role", "muted")
        model_label.setProperty("size", "small")
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
        self.start_server_btn = QPushButton(tr("ai_start_server"))
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
        self.progress_label.setProperty("size", "small")
        self.progress_label.setVisible(False)
        layout.addWidget(self.progress_label)

        # Clean Text button
        self.clean_btn = QPushButton(tr("ai_clean_text"))
        self.clean_btn.setStyleSheet("text-align: left;")
        self.clean_btn.setToolTip(tr("ai_clean_text_tooltip"))
        self.clean_btn.clicked.connect(self._on_clean_clicked)
        self.clean_btn.setEnabled(False)
        layout.addWidget(self.clean_btn)

        # Generate Articles section
        articles_layout = QHBoxLayout()
        articles_layout.setSpacing(4)

        self.generate_btn = QPushButton(tr("ai_generate"))
        self.generate_btn.setStyleSheet("text-align: left;")
        self.generate_btn.setToolTip(tr("ai_generate_tooltip"))
        self.generate_btn.clicked.connect(self._on_generate_clicked)
        self.generate_btn.setEnabled(False)
        articles_layout.addWidget(self.generate_btn, stretch=1)

        # Format selector
        self.format_combo = QComboBox()

        # Add format options
        for fmt in ArticleFormat:
            info = ARTICLE_FORMAT_INFO[fmt]
            display_text = f"{info['icon']} {tr(f'ai_format_{fmt.value}')}"
            self.format_combo.addItem(display_text, fmt.value)

        # Size to the longest entry rather than a hard-coded width, which
        # truncated labels ("📝 Blog" → "📝 Bl") as soon as the theme's
        # font size grew.
        self.format_combo.setFixedWidth(self.format_combo.sizeHint().width())

        articles_layout.addWidget(self.format_combo)
        layout.addLayout(articles_layout)

        # Generate All button
        self.generate_all_btn = QPushButton(tr("ai_generate_all"))
        self.generate_all_btn.setStyleSheet("text-align: left;")
        self.generate_all_btn.setToolTip(tr("ai_generate_all_tooltip"))
        self.generate_all_btn.clicked.connect(self._on_generate_all_clicked)
        self.generate_all_btn.setEnabled(False)
        layout.addWidget(self.generate_all_btn)

        layout.addStretch()

    def _start_connection_check(self):
        """Start periodic connection check."""
        QTimer.singleShot(0, self._check_connection)

        # Check connection every 10 seconds
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self._check_connection)
        self.check_timer.start(10000)

    def _check_connection(self):
        """Schedule a non-blocking LM Studio connection check."""
        if self._processing:
            return  # Don't check while processing
        self._submit_task("status")

    def _refresh_models(self):
        """Schedule a non-blocking refresh of the model dropdown."""
        self._submit_task("models")

    def _on_model_selected(self, index: int):
        """Handle model selection change."""
        if index < 0:
            return

        model_path = self.model_combo.itemData(index)
        if not model_path:
            return

        if self._current_model and model_path in self._current_model:
            return  # Already loaded

        # Load the selected model
        self.status_indicator.label.setText(tr("ai_loading_model"))
        set_role(self.status_indicator.label, "warning-text")
        self.status_indicator.label.setProperty("size", "small")

        self._load_model(model_path)

    def _load_model(self, model_path: str):
        """Schedule a cancellable model load."""
        self._submit_task("load", model_path, priority=True)

    def _start_server(self):
        """Start LM Studio server."""
        self.start_server_btn.setEnabled(False)
        self.start_server_btn.setText(tr("ai_server_starting"))
        self._do_start_server()

    def _do_start_server(self):
        """Schedule a cancellable server start."""
        self._submit_task("start", priority=True)

    def _submit_task(self, action: str, payload=None, priority: bool = False) -> bool:
        """Serialize all LM Studio operations; periodic checks never overlap."""
        if self._closing:
            return False
        if self._worker and self._worker.isRunning():
            if action == "status" and not priority:
                return False
            self._pending_task = (action, payload)
            if priority:
                self._worker.cancel()
            return False

        worker = LMStudioTaskWorker(
            action, self._processor, self._manager, payload, self
        )
        self._worker = worker
        worker.result_ready.connect(self._on_task_result)
        worker.failed.connect(self._on_task_failed)
        worker.finished.connect(lambda current=worker: self._on_task_finished(current))
        worker.start()
        return True

    def _on_task_result(self, action: str, result) -> None:
        if self._closing or result is None:
            return
        if action == "status":
            connected = bool(result["connected"])
            self.status_indicator.set_connected(connected, result["model_name"])
            cli_available = bool(result["cli_available"])
            self.model_row.setVisible(cli_available and connected)
            self.start_server_btn.setVisible(cli_available and not connected)
            self.start_server_btn.setEnabled(True)
            self.start_server_btn.setText(tr("ai_start_server"))
            self._update_button_states()
            model_changed = bool(
                result["model_name"]
                and result["model_name"] != self._current_model
            )
            if connected and cli_available and (
                self.model_combo.count() == 0 or model_changed
            ):
                self._pending_task = ("models", None)
        elif action == "models":
            self._apply_models(result["models"], result["current"])
        elif action == "load":
            if result:
                self._pending_task = ("status", None)
            else:
                self.status_indicator.label.setText(tr("ai_model_load_failed"))
                set_role(self.status_indicator.label, "danger-text")
                self.status_indicator.label.setProperty("size", "small")
        elif action == "start":
            if result:
                self._pending_task = ("status", None)
            else:
                self.start_server_btn.setText(tr("ai_server_retry"))
                self.start_server_btn.setEnabled(True)

    def _apply_models(self, models, current_model: str | None) -> None:
        self._current_model = current_model
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        current_index = 0
        for i, model in enumerate(models):
            self.model_combo.addItem(model.display_name, model.path)
            if current_model and model.path in current_model:
                current_index = i
        if self.model_combo.count() > 0:
            self.model_combo.setCurrentIndex(current_index)
        self.model_combo.blockSignals(False)

    def _on_task_failed(self, action: str, message: str) -> None:
        logger.warning("LM Studio %s task failed: %s", action, message)
        if action == "start":
            self.start_server_btn.setText(tr("ai_server_retry"))
            self.start_server_btn.setEnabled(True)
        elif action == "load":
            self.status_indicator.label.setText(tr("ai_model_load_failed"))

    def _on_task_finished(self, worker: LMStudioTaskWorker) -> None:
        if self._worker is not worker:
            return
        self._worker = None
        worker.deleteLater()
        if self._closing or self._pending_task is None:
            return
        action, payload = self._pending_task
        self._pending_task = None
        QTimer.singleShot(0, lambda: self._submit_task(action, payload))

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
