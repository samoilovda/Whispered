"""
Whisper Fedora UI - Transcript View Widget
Display transcription results with timestamps and speaker labels
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QLabel, QHBoxLayout,
    QPushButton, QFrame
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QTextCharFormat, QColor, QTextCursor

from transcriber import TranscriptionResult
from utils import format_timestamp_vtt
from ui.icons import get_icon, IconColors


# Speaker color palette
SPEAKER_COLORS = {
    "Speaker 1": "#6366f1",  # Purple
    "Speaker 2": "#22c55e",  # Green
    "Speaker 3": "#f59e0b",  # Orange
    "Speaker 4": "#ef4444",  # Red
    "Speaker 5": "#06b6d4",  # Cyan
    "Speaker 6": "#ec4899",  # Pink
    "Speaker 7": "#84cc16",  # Lime
    "Speaker 8": "#8b5cf6",  # Violet
}

class TranscriptView(QWidget):
    """Widget to display transcription results."""
    
    # Signal emitted when copy button is clicked
    copy_requested = pyqtSignal()
    export_requested = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: TranscriptionResult | None = None
        self._show_timestamps = True
        self._show_speakers = True
        self._setup_ui()
    
    def _setup_ui(self):
        """Set up the UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        
        # Header with controls
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        
        title = QLabel("Transcription")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        # Toggle timestamps button with vector icon
        self.timestamps_btn = QPushButton("Timestamps")
        self.timestamps_btn.setIcon(get_icon('clock', IconColors.DEFAULT, 14))
        self.timestamps_btn.setCheckable(True)
        self.timestamps_btn.setChecked(True)
        self.timestamps_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 4px 12px;
                color: #888;
            }
            QPushButton:checked {
                border-color: #6366f1;
                color: #6366f1;
            }
            QPushButton:hover {
                background-color: rgba(99, 102, 241, 0.1);
            }
        """)
        self.timestamps_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timestamps_btn.clicked.connect(self._toggle_timestamps)
        header_layout.addWidget(self.timestamps_btn)
        
        # Toggle speakers button
        self.speakers_btn = QPushButton("Speakers")
        self.speakers_btn.setIcon(get_icon('user', IconColors.DEFAULT, 14))
        self.speakers_btn.setCheckable(True)
        self.speakers_btn.setChecked(True)
        self.speakers_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 4px 12px;
                color: #888;
            }
            QPushButton:checked {
                border-color: #22c55e;
                color: #22c55e;
            }
            QPushButton:hover {
                background-color: rgba(34, 197, 94, 0.1);
            }
        """)
        self.speakers_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speakers_btn.clicked.connect(self._toggle_speakers)
        self.speakers_btn.setVisible(False)  # Only show when diarization available
        header_layout.addWidget(self.speakers_btn)
        
        # Copy button with vector icon
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setIcon(get_icon('clipboard', IconColors.DEFAULT, 14))
        self.copy_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: 1px solid #4a4a4a;
                border-radius: 4px;
                padding: 4px 12px;
                color: #888;
            }
            QPushButton:hover {
                border-color: #6366f1;
                color: #6366f1;
                background-color: rgba(99, 102, 241, 0.1);
            }
        """)
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self.copy_requested.emit)
        header_layout.addWidget(self.copy_btn)
        
        # Export button with vector icon
        self.export_btn = QPushButton("Export")
        self.export_btn.setIcon(get_icon('save', IconColors.WHITE, 14))
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #6366f1;
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                color: white;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #818cf8;
            }
        """)
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.clicked.connect(self.export_requested.emit)
        header_layout.addWidget(self.export_btn)
        
        layout.addWidget(header)
        
        # Text display
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Monospace", 11))
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: #1e1e1e;
                border: 1px solid #3a3a3a;
                border-radius: 8px;
                padding: 12px;
                line-height: 1.6;
            }
        """)
        self.text_edit.setPlaceholderText("Transcription will appear here...")
        layout.addWidget(self.text_edit, stretch=1)
        
        # Stats bar
        self.stats_bar = QWidget()
        self.stats_bar.setVisible(False)
        stats_layout = QHBoxLayout(self.stats_bar)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("color: #888; font-size: 11px;")
        stats_layout.addWidget(self.stats_label)
        
        stats_layout.addStretch()
        
        layout.addWidget(self.stats_bar)
        
        # Initially disable buttons
        self._set_buttons_enabled(False)
    
    def _set_buttons_enabled(self, enabled: bool):
        """Enable or disable action buttons."""
        self.copy_btn.setEnabled(enabled)
        self.export_btn.setEnabled(enabled)
        self.timestamps_btn.setEnabled(enabled)
    
    def _toggle_timestamps(self):
        """Toggle timestamp display."""
        self._show_timestamps = self.timestamps_btn.isChecked()
        self._update_display()
    
    def _toggle_speakers(self):
        """Toggle speaker label display."""
        self._show_speakers = self.speakers_btn.isChecked()
        self._update_display()
    
    def _get_speaker_color(self, speaker: str) -> str:
        """Get color for a speaker."""
        if speaker in SPEAKER_COLORS:
            return SPEAKER_COLORS[speaker]
        # Generate color for unknown speakers
        return "#888888"
    
    def _update_display(self):
        """Update the text display based on current settings."""
        if not self._result:
            return
        
        # Check if any segments have speaker labels
        has_speakers = any(seg.speaker for seg in self._result.segments)
        self.speakers_btn.setVisible(has_speakers)
        
        # Build display text with optional HTML formatting
        if has_speakers and self._show_speakers:
            self._update_display_with_speakers()
        else:
            self._update_display_plain()
    
    def _update_display_plain(self):
        """Update display without speaker colors."""
        lines = []
        if self._show_timestamps:
            for seg in self._result.segments:
                timestamp = format_timestamp_vtt(seg.start)
                lines.append(f"[{timestamp}]  {seg.text.strip()}")
        else:
            for seg in self._result.segments:
                lines.append(seg.text.strip())
        
        self.text_edit.setText('\n'.join(lines))
    
    def _update_display_with_speakers(self):
        """Update display with colored speaker labels."""
        html_lines = []
        
        for seg in self._result.segments:
            parts = []
            
            # Timestamp
            if self._show_timestamps:
                timestamp = format_timestamp_vtt(seg.start)
                parts.append(f'<span style="color: #666;">[{timestamp}]</span>')
            
            # Speaker label
            speaker = seg.speaker or "Unknown"
            color = self._get_speaker_color(speaker)
            parts.append(f'<span style="color: {color}; font-weight: bold;">[{speaker}]</span>')
            
            # Text
            parts.append(f'<span style="color: #e0e0e0;">{seg.text.strip()}</span>')
            
            html_lines.append(' '.join(parts))
        
        html = '<br>'.join(html_lines)
        self.text_edit.setHtml(html)
    
    def set_result(self, result: TranscriptionResult):
        """Set the transcription result to display."""
        self._result = result
        self._update_display()
        self._set_buttons_enabled(True)
        
        # Update stats
        word_count = len(result.full_text.split())
        segment_count = len(result.segments)
        duration_min = result.duration / 60
        
        self.stats_label.setText(
            f"{segment_count} segments  •  {word_count} words  •  {duration_min:.1f} min"
        )
        self.stats_bar.setVisible(True)
    
    def clear(self):
        """Clear the display."""
        self._result = None
        self.text_edit.clear()
        self._set_buttons_enabled(False)
        self.stats_bar.setVisible(False)
    
    def get_text(self) -> str:
        """Get the current display text."""
        return self.text_edit.toPlainText()
    
    def get_result(self) -> TranscriptionResult | None:
        """Get the current transcription result."""
        return self._result
