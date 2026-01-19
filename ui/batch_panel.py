"""
Whisper Fedora UI - Batch Processing Panel
Widget for managing batch file queue
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QListWidget, QListWidgetItem, QProgressBar,
    QFileDialog, QFrame, QAbstractItemView
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor

from batch_processor import BatchProcessor, BatchItem, BatchStatus


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
        
        header_label = QLabel("📂 Batch Queue")
        header_label.setStyleSheet("color: #888; font-size: 12px; font-weight: bold;")
        header_layout.addWidget(header_label)
        
        self.count_label = QLabel("(0)")
        self.count_label.setStyleSheet("color: #666; font-size: 11px;")
        header_layout.addWidget(self.count_label)
        
        header_layout.addStretch()
        
        # Add files button
        self.add_btn = QPushButton("+")
        self.add_btn.setFixedSize(24, 24)
        self.add_btn.setToolTip("Add files to queue")
        self.add_btn.setStyleSheet("""
            QPushButton {
                background-color: #2a2a2a;
                border: 1px solid #3a3a3a;
                border-radius: 4px;
                color: #888;
                font-size: 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #3a3a3a;
                color: #e0e0e0;
            }
        """)
        self.add_btn.clicked.connect(self._add_files)
        header_layout.addWidget(self.add_btn)
        
        layout.addLayout(header_layout)
        
        # File list
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(150)
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
        layout.addWidget(self.file_list)
        
        # Action buttons
        button_style = """
            QPushButton {
                background-color: #2a2a2a;
                border: 1px solid #3a3a3a;
                border-radius: 6px;
                padding: 6px 12px;
                color: #e0e0e0;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3a3a3a;
            }
            QPushButton:disabled {
                background-color: #1a1a1a;
                color: #555;
            }
        """
        
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(4)
        
        self.start_btn = QPushButton("▶ Start All")
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                color: white;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #818cf8;
            }
            QPushButton:disabled {
                background-color: #3a3a3a;
                color: #666;
            }
        """)
        self.start_btn.clicked.connect(self._start_batch)
        self.start_btn.setEnabled(False)
        actions_layout.addWidget(self.start_btn)
        
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setStyleSheet(button_style)
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
            "Add Audio/Video Files",
            "",
            "Media Files (*.mp3 *.mp4 *.m4a *.wav *.ogg *.flac *.mkv *.avi *.mov *.webm);;All Files (*)"
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
        self.count_label.setText(f"({count})")
        self.start_btn.setEnabled(count > 0 and not self.processor.is_processing)
        self.clear_btn.setEnabled(count > 0 and not self.processor.is_processing)
    
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
        self.start_requested.emit()
    
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
        self.add_btn.setEnabled(True)
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
