"""
Whispered UI - Batch Processing Panel
Widget for managing batch file queue
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QListWidget, QListWidgetItem, QProgressBar,
    QFileDialog, QAbstractItemView
)
from PyQt6.QtCore import pyqtSignal

from batch_processor import BatchProcessor, BatchItem, BatchStatus
from ui.theme import set_role
from ui.empty_state import EmptyStateWidget
from core.i18n import tr


STATUS_ICONS = {
    BatchStatus.PENDING: "○",
    BatchStatus.PROCESSING: "●",
    BatchStatus.COMPLETE: "✓",
    BatchStatus.ERROR: "×",
    BatchStatus.CANCELLED: "!",
}


# ============================================================================
# BATCH ITEM WIDGET
# ============================================================================

class BatchItemWidget(QWidget):
    """Widget representing a single batch item."""

    remove_requested = pyqtSignal(int)

    def __init__(self, index: int, item: BatchItem, parent=None):
        super().__init__(parent)
        self.index = index
        self.item = item
        self._setup_ui()
        self.update_display()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # Status icon
        self.status_label = QLabel()
        self.status_label.setFixedWidth(20)
        layout.addWidget(self.status_label)

        # Filename
        self.name_label = QLabel()
        self.name_label.setProperty("role", "muted")
        layout.addWidget(self.name_label, stretch=1)

        # Progress bar (only visible during processing)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(80)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Remove button
        self.remove_btn = QPushButton("×")
        self.remove_btn.setFixedSize(20, 20)
        self.remove_btn.setProperty("variant", "danger")
        self.remove_btn.setAccessibleName(tr("batch_remove"))
        self.remove_btn.setToolTip(tr("batch_remove"))
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self.index))
        layout.addWidget(self.remove_btn)

    def update_display(self):
        """Update the display based on current item state."""
        # Status icon
        icon = STATUS_ICONS.get(self.item.status, "?")
        self.status_label.setText(icon)

        # Filename with color
        self.name_label.setText(self.item.filename)
        role = {
            BatchStatus.PENDING: "muted",
            BatchStatus.PROCESSING: "warning-text",
            BatchStatus.COMPLETE: "success-text",
            BatchStatus.ERROR: "danger-text",
            BatchStatus.CANCELLED: "warning-text",
        }.get(self.item.status, "muted")
        set_role(self.name_label, role)
        set_role(self.status_label, role)

        # Progress bar
        if self.item.status == BatchStatus.PROCESSING:
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(self.item.progress)
            self.remove_btn.setEnabled(False)
        else:
            self.progress_bar.setVisible(False)
            self.remove_btn.setEnabled(True)


# ============================================================================
# BATCH PANEL
# ============================================================================

class BatchPanel(QWidget):
    """Panel for batch file management."""

    # Signals
    start_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.processor = BatchProcessor()
        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # Header
        header_layout = QHBoxLayout()

        header_label = QLabel(tr("batch_summary_title"))
        header_label.setProperty("role", "section-title")
        header_layout.addWidget(header_label)

        self.count_label = QLabel(tr("batch_summary", pending=0, complete=0, error=0))
        self.count_label.setProperty("role", "dim")
        header_layout.addWidget(self.count_label)

        header_layout.addStretch()

        # Add files button
        self.add_btn = QPushButton(tr("batch_add_files"))
        self.add_btn.setToolTip(tr("batch_add_files"))
        self.add_btn.clicked.connect(self._add_files)
        header_layout.addWidget(self.add_btn)

        layout.addLayout(header_layout)

        # File list
        self.file_list = QListWidget()
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        layout.addWidget(self.file_list, stretch=1)

        self.empty_state = EmptyStateWidget(
            "layers",
            tr("batch_empty_title"),
            tr("batch_empty_hint"),
            tr("batch_add_files"),
        )
        self.empty_state.action_button.clicked.connect(self._add_files)
        layout.addWidget(self.empty_state, stretch=1)

        # Action buttons
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(4)

        self.start_btn = QPushButton(tr("batch_start_all"))
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.clicked.connect(self._start_batch)
        self.start_btn.setEnabled(False)
        actions_layout.addWidget(self.start_btn)

        self.cancel_btn = QPushButton(tr("btn_cancel"))
        self.cancel_btn.setProperty("variant", "danger")
        self.cancel_btn.clicked.connect(self.cancel_processing)
        self.cancel_btn.setVisible(False)
        actions_layout.addWidget(self.cancel_btn)

        self.clear_btn = QPushButton(tr("batch_clear"))
        self.clear_btn.clicked.connect(self._clear_queue)
        self.clear_btn.setEnabled(False)
        actions_layout.addWidget(self.clear_btn)

        layout.addLayout(actions_layout)

    def _connect_signals(self):
        """Connect processor signals."""
        self.processor.item_started.connect(self._on_item_started)
        self.processor.item_progress.connect(self._on_item_progress)
        self.processor.item_finished.connect(self._on_item_finished)
        self.processor.item_error.connect(self._on_item_error)
        self.processor.batch_finished.connect(self._on_batch_finished)

    def _add_files(self):
        """Open file dialog to add files."""
        filepaths, _ = QFileDialog.getOpenFileNames(
            self,
            tr("batch_add_files_title"),
            "",
            tr("batch_file_filter")
        )

        for path in filepaths:
            self.processor.add_file(path)

        self._refresh_list()

    def _refresh_list(self):
        """Refresh the file list display."""
        self.file_list.clear()

        for i, item in enumerate(self.processor.items):
            widget = BatchItemWidget(i, item)
            widget.remove_requested.connect(self._remove_item)

            list_item = QListWidgetItem(self.file_list)
            list_item.setSizeHint(widget.sizeHint())
            self.file_list.addItem(list_item)
            self.file_list.setItemWidget(list_item, widget)

        # Update counts and buttons
        count = self.processor.count
        complete = sum(item.status == BatchStatus.COMPLETE for item in self.processor.items)
        errors = sum(item.status == BatchStatus.ERROR for item in self.processor.items)
        pending = max(0, count - complete - errors)
        self.count_label.setText(
            tr("batch_summary", pending=pending, complete=complete, error=errors)
        )
        self.start_btn.setEnabled(count > 0 and not self.processor.is_processing)
        self.clear_btn.setEnabled(count > 0 and not self.processor.is_processing)
        self.file_list.setVisible(count > 0)
        self.empty_state.setVisible(count == 0)
        self.cancel_btn.setVisible(self.processor.is_processing)

    def _remove_item(self, index: int):
        """Remove an item from the queue."""
        self.processor.remove_item(index)
        self._refresh_list()

    def _clear_queue(self):
        """Clear all items from the queue."""
        self.processor.clear()
        self._refresh_list()

    def _start_batch(self):
        """Emit signal to start batch processing."""
        self.start_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.add_btn.setEnabled(False)
        self.cancel_btn.setVisible(True)
        self.start_requested.emit()

    def start_processing(
        self,
        model_name: str,
        language: str = 'auto',
        translate: bool = False,
        n_threads: int = 4,
        enable_diarization: bool = False,
        num_speakers: int = None,
        use_gpu: bool = True,
    ):
        """Start the batch processing with given settings."""
        self.processor.start(
            model_name=model_name,
            language=language,
            translate=translate,
            n_threads=n_threads,
            enable_diarization=enable_diarization,
            num_speakers=num_speakers,
            use_gpu=use_gpu,
        )

    def cancel_processing(self):
        """Cancel the current batch processing."""
        self.processor.cancel()

    def _on_item_started(self, index: int):
        """Handle item started."""
        self._update_item_widget(index)

    def _on_item_progress(self, index: int, progress: int, message: str):
        """Handle item progress update."""
        self._update_item_widget(index)

    def _on_item_finished(self, index: int, result):
        """Handle item completion."""
        self._update_item_widget(index)

    def _on_item_error(self, index: int, error: str):
        """Handle item error."""
        self._update_item_widget(index)

    def _on_batch_finished(self):
        """Handle batch completion."""
        self.start_btn.setEnabled(self.processor.count > 0)
        self.clear_btn.setEnabled(self.processor.count > 0)
        self.add_btn.setEnabled(True)
        self.cancel_btn.setVisible(False)
        self._refresh_list()

    def _update_item_widget(self, index: int):
        """Update a specific item widget."""
        if index < 0 or index >= self.file_list.count():
            return

        list_item = self.file_list.item(index)
        widget = self.file_list.itemWidget(list_item)
        if isinstance(widget, BatchItemWidget):
            widget.item = self.processor.items[index]
            widget.update_display()

    def get_results(self):
        """Get all completed results."""
        return self.processor.get_results()

    def export_all(self, output_dir: str, format_key: str = 'txt'):
        """Export all completed transcriptions."""
        return self.processor.export_all(output_dir, format_key)
