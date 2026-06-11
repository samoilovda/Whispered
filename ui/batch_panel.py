"""
Whisper Fedora UI - Batch Processing Panel
Widget for managing batch file queue
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QListWidget, QListWidgetItem, QProgressBar,
    QFileDialog, QFrame, QAbstractItemView, QMessageBox, QComboBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor

from batch_processor import BatchProcessor, BatchItem, BatchStatus
from core.i18n import tr


# ============================================================================
# STATUS COLORS
# ============================================================================

STATUS_COLORS = {
    BatchStatus.PENDING: "#888888",
    BatchStatus.PROCESSING: "#6366f1",
    BatchStatus.COMPLETE: "#22c55e",
    BatchStatus.ERROR: "#ef4444",
    BatchStatus.CANCELLED: "#f59e0b",
}

STATUS_ICONS = {
    BatchStatus.PENDING: "⏸️",
    BatchStatus.PROCESSING: "⏳",
    BatchStatus.COMPLETE: "✅",
    BatchStatus.ERROR: "❌",
    BatchStatus.CANCELLED: "⚠️",
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
        self.name_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.name_label, stretch=1)
        
        # Progress bar (only visible during processing)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(80)
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar { 
                border: none; 
                border-radius: 4px; 
                background-color: #2a2a2a; 
            }
            QProgressBar::chunk { 
                background-color: #6366f1; 
                border-radius: 4px; 
            }
        """)
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Remove button
        self.remove_btn = QPushButton("×")
        self.remove_btn.setFixedSize(20, 20)
        self.remove_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                color: #888;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                color: #ef4444;
            }
        """)
        self.remove_btn.clicked.connect(lambda: self.remove_requested.emit(self.index))
        layout.addWidget(self.remove_btn)
    
    def update_display(self):
        """Update the display based on current item state."""
        # Status icon
        icon = STATUS_ICONS.get(self.item.status, "?")
        self.status_label.setText(icon)
        
        # Filename with color
        color = STATUS_COLORS.get(self.item.status, "#888")
        self.name_label.setText(self.item.filename)
        self.name_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        
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
        
        # Timer for ETA updates
        self._eta_timer = QTimer(self)
        self._eta_timer.timeout.connect(self._update_eta)
        self._eta_timer.setInterval(1000)
    
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
        
        # Header
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        title_label = QLabel(tr("Batch Queue"))
        title_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #e0e0e0;")
        header_layout.addWidget(title_label)
        
        # Mode selector
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([tr("Posts Pipeline"), tr("Book Pipeline")])
        self.mode_combo.setStyleSheet("color: #666; font-size: 11px;")
        header_layout.addWidget(self.mode_combo)
        
        header_layout.addStretch()
        
        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)
        
        self.add_btn = QPushButton(tr("Add Files"))
        self.add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_btn.clicked.connect(self._add_files)
        btn_layout.addWidget(self.add_btn)
        
        self.clear_btn = QPushButton(tr("Clear"))
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear_queue)
        btn_layout.addWidget(self.clear_btn)
        
        self.pause_btn = QPushButton(tr("⏸️ Pause"))
        self.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pause_btn.setCheckable(True)
        self.pause_btn.clicked.connect(self._toggle_pause)
        btn_layout.addWidget(self.pause_btn)
        
        self.start_btn = QPushButton(tr("Start All"))
        self.start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_btn.clicked.connect(self._start_batch)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4f46e5;
            }
            QPushButton:disabled {
                background-color: #374151;
                color: #9ca3af;
            }
        """)
        btn_layout.addWidget(self.start_btn)
        
        layout.addLayout(header_layout)
        layout.addLayout(btn_layout)
        
        # File list
        self.file_list = QListWidget()
        self.file_list.setStyleSheet("""
            QListWidget {
                background-color: #1e1e1e;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
                padding: 4px;
            }
            QListWidget::item {
                background: transparent;
                border-radius: 4px;
                padding: 2px;
            }
            QListWidget::item:selected {
                background-color: #2a2a2a;
            }
        """)
        self.file_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.file_list.model().rowsMoved.connect(self._on_rows_moved)
        layout.addWidget(self.file_list)
        
        self.eta_label = QLabel("")
        self.eta_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.eta_label)
    
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
            tr("Add Audio/Video Files"),
            "",
            tr("Media Files (*.mp3 *.mp4 *.m4a *.wav *.ogg *.flac *.mkv *.avi *.mov *.webm);;All Files (*)")
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
        
        count = self.processor.count
        self.start_btn.setEnabled(count > 0 and not self.processor.is_processing)
        self.clear_btn.setEnabled(count > 0 and not self.processor.is_processing)
        self.pause_btn.setEnabled(self.processor.is_processing)
    
    def _remove_item(self, index: int):
        """Remove an item from the queue."""
        self.processor.remove_item(index)
        self._refresh_list()
    
    def _clear_queue(self):
        """Clear all items from the queue (with confirmation)."""
        count = self.processor.count
        if count == 0:
            return
        reply = QMessageBox.question(
            self, tr("Clear Queue"),
            tr("Remove all files from the batch queue?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.processor.clear()
        self._refresh_list()
    
    def _start_batch(self):
        """Emit signal to start batch processing."""
        self.start_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.add_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.pause_btn.setText(tr("⏸️ Pause"))
        self._eta_timer.start()
        self.start_requested.emit()
        
    def _toggle_pause(self):
        """Toggle pause/resume on processor."""
        is_paused = self.processor.is_paused
        if not is_paused:
            self.pause_btn.setText(tr("▶️ Resume"))
            self.pause_btn.setStyleSheet("background-color: #eab308; color: #000; font-weight: bold;")
            self.processor.pause()
            self.eta_label.setText(tr("Paused"))
        else:
            self.pause_btn.setText(tr("⏸️ Pause"))
            self.pause_btn.setStyleSheet("")
            self.processor.resume()
            self.eta_label.setText("")
            
    def _update_eta(self):
        """Update ETA label."""
        eta = self.processor.get_eta_seconds()
        if eta is not None:
            mins, secs = divmod(int(eta), 60)
            self.eta_label.setText(f"{tr('ETA')}: ~{mins}m {secs}s")
        else:
            self.eta_label.setText(f"{tr('ETA')}: {tr('calc...')}...")
            
    def _on_rows_moved(self, sourceParent, sourceStart, sourceEnd, destinationParent, destinationRow):
        """Sync reordering from QListWidget to Processor."""
        if sourceStart < destinationRow:
            destinationRow -= 1
        self.processor.reorder_items(sourceStart, destinationRow)
    
    def start_processing(
        self,
        model_name: str,
        language: str = 'auto',
        translate: bool = False,
        n_threads: int = 4,
        enable_diarization: bool = False,
        num_speakers: int = None
    ):
        """Start the batch processing with given settings."""
        self.processor.start(
            model_name=model_name,
            language=language,
            translate=translate,
            n_threads=n_threads,
            enable_diarization=enable_diarization,
            num_speakers=num_speakers
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
        self.pause_btn.setEnabled(False)
        self.add_btn.setEnabled(True)
        self._eta_timer.stop()
        self.eta_label.setText(tr("Complete"))
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
