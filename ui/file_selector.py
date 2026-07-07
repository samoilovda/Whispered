"""
Whisper Fedora UI - File Selector Widget
Drag-and-drop file upload with format validation
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog, QHBoxLayout
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from utils import is_supported_format, SUPPORTED_FORMATS, get_audio_duration, format_duration
from ui.icons import IconLabel, get_icon, IconColors


class FileSelector(QWidget):
    """Drag-and-drop file selector widget."""

    # Signal emitted when a valid file is selected
    file_selected = pyqtSignal(str)  # filepath

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_file = None
        self._setup_ui()
        self.setAcceptDrops(True)

    def _setup_ui(self):
        """Set up the UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Drop zone container
        self.drop_zone = QWidget()
        self.drop_zone.setObjectName("dropZone")
        self.drop_zone.setStyleSheet("""
            #dropZone {
                border: 2px dashed #4a4a4a;
                border-radius: 12px;
                background-color: rgba(99, 102, 241, 0.05);
                min-height: 180px;
            }
            #dropZone:hover {
                border-color: #6366f1;
                background-color: rgba(99, 102, 241, 0.1);
            }
        """)

        drop_layout = QVBoxLayout(self.drop_zone)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.setSpacing(16)

        # Vector icon label
        self.icon_label = IconLabel('music', IconColors.DEFAULT, 48)
        drop_layout.addWidget(self.icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Text label
        self.text_label = QLabel("Drop audio or video file here")
        self.text_label.setStyleSheet("color: #888; font-size: 14px;")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(self.text_label)

        # Browse button
        self.browse_btn = QPushButton("Browse Files")
        self.browse_btn.setProperty("variant", "primary")
        self.browse_btn.setToolTip("Open file  (Ctrl+O)")
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self._browse_files)
        drop_layout.addWidget(self.browse_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Formats hint
        formats_hint = QLabel("Supports: MP3, WAV, FLAC, MP4, MKV, and more")
        formats_hint.setStyleSheet("color: #666; font-size: 11px;")
        formats_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(formats_hint)

        layout.addWidget(self.drop_zone)

        # Selected file info (hidden initially)
        self.file_info = QWidget()
        self.file_info.setVisible(False)
        self.file_info.setStyleSheet("""
            QWidget {
                background-color: rgba(99, 102, 241, 0.1);
                border-radius: 8px;
            }
        """)
        file_info_layout = QHBoxLayout(self.file_info)
        file_info_layout.setContentsMargins(12, 10, 12, 10)

        # File icon
        self.file_icon = IconLabel('file', IconColors.PRIMARY, 18)
        file_info_layout.addWidget(self.file_icon)

        self.file_name_label = QLabel()
        self.file_name_label.setStyleSheet("font-weight: bold; margin-left: 8px;")
        file_info_layout.addWidget(self.file_name_label, stretch=1)

        self.file_duration_label = QLabel()
        self.file_duration_label.setStyleSheet("color: #888;")
        file_info_layout.addWidget(self.file_duration_label)

        self.clear_btn = QPushButton()
        self.clear_btn.setIcon(get_icon('close', IconColors.DEFAULT, 14))
        self.clear_btn.setFixedSize(24, 24)
        self.clear_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
            }
            QPushButton:hover {
                background: rgba(248, 113, 113, 0.1);
                border-radius: 12px;
            }
        """)
        self.clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_btn.clicked.connect(self._clear_selection)
        file_info_layout.addWidget(self.clear_btn)

        layout.addWidget(self.file_info)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """Handle drag enter event."""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                filepath = urls[0].toLocalFile()
                if is_supported_format(filepath):
                    event.acceptProposedAction()
                    self.drop_zone.setStyleSheet("""
                        #dropZone {
                            border: 2px dashed #6366f1;
                            border-radius: 12px;
                            background-color: rgba(99, 102, 241, 0.15);
                            min-height: 180px;
                        }
                    """)
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        """Handle drag leave event."""
        self._reset_drop_zone_style()

    def dropEvent(self, event: QDropEvent):
        """Handle drop event."""
        self._reset_drop_zone_style()

        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0]
            if url.isLocalFile():
                filepath = url.toLocalFile()
                if is_supported_format(filepath):
                    self._set_file(filepath)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def _reset_drop_zone_style(self):
        """Reset drop zone to default style."""
        self.drop_zone.setStyleSheet("""
            #dropZone {
                border: 2px dashed #4a4a4a;
                border-radius: 12px;
                background-color: rgba(99, 102, 241, 0.05);
                min-height: 180px;
            }
            #dropZone:hover {
                border-color: #6366f1;
                background-color: rgba(99, 102, 241, 0.1);
            }
        """)

    def _browse_files(self):
        """Open file browser dialog."""
        formats = ' '.join(f'*{ext}' for ext in SUPPORTED_FORMATS)
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Select Audio or Video File",
            "",
            f"Media Files ({formats});;All Files (*)"
        )
        if filepath:
            self._set_file(filepath)


    def _set_file(self, filepath: str):
        """Set the selected file."""
        # Verify file exists
        if not os.path.isfile(filepath):
            return

        self.selected_file = filepath

        # Update display
        filename = os.path.basename(filepath)
        self.file_name_label.setText(filename)

        # Get duration if possible
        duration = get_audio_duration(filepath)
        if duration:
            self.file_duration_label.setText(format_duration(duration))
        else:
            self.file_duration_label.setText("")

        # Show file info, update drop zone with success state
        self.file_info.setVisible(True)
        self.icon_label.set_icon('check_circle')
        self.icon_label.set_color(IconColors.SUCCESS)
        self.text_label.setText("File ready for transcription")
        self.text_label.setStyleSheet("color: #22c55e; font-size: 14px;")

        # Emit signal
        self.file_selected.emit(filepath)

    def _clear_selection(self):
        """Clear the current selection."""
        self.selected_file = None
        self.file_info.setVisible(False)
        self.icon_label.set_icon('music')
        self.icon_label.set_color(IconColors.DEFAULT)
        self.text_label.setText("Drop audio or video file here")
        self.text_label.setStyleSheet("color: #888; font-size: 14px;")

    def get_file(self) -> str | None:
        """Get the currently selected file path."""
        return self.selected_file
