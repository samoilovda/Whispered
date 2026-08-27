"""
Whispered UI - File Selector Widget
Drag-and-drop file upload with format validation
"""

import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog, QHBoxLayout
)
from PyQt6.QtCore import QProcess, Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent

from utils import is_supported_format, SUPPORTED_FORMATS, format_duration
from ui.icons import IconLabel, get_icon, IconColors
from ui.theme import set_role
from core.i18n import tr
from ui.i18n_helpers import Retranslator
from core.external_tools import resolve_tool


class ElidedLabel(QLabel):
    """QLabel that elides its text with '…' instead of demanding the full
    text width from the layout — a long filename must not balloon the
    panel it sits in."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._update_elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elide()

    def _update_elide(self) -> None:
        metrics = self.fontMetrics()
        elided = metrics.elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, max(self.width(), 1)
        )
        super().setText(elided)

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        size.setWidth(0)
        return size

    def sizeHint(self):
        size = super().sizeHint()
        size.setWidth(0)
        return size


class FileSelector(QWidget):
    """Drag-and-drop file selector widget."""

    # Signal emitted when a valid file is selected
    file_selected = pyqtSignal(str)  # filepath
    # Signal emitted when the selected file is explicitly cleared.
    file_cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_file = None
        self._compact = False
        self._probe_path: str | None = None
        self._pending_probe_path: str | None = None
        self._duration_probe = QProcess(self)
        self._duration_probe.finished.connect(self._on_duration_probe_finished)
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.call(self._retranslate_selector)
        self._i18n.bind()
        self.setAcceptDrops(True)

    def _retranslate_selector(self) -> None:
        self.text_label.setText(
            tr("file_ready") if self.selected_file else tr("file_drop_title")
        )

    def _setup_ui(self):
        """Set up the UI components."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Drop zone container
        self.drop_zone = QWidget()
        self.drop_zone.setProperty("role", "drop-zone")

        drop_layout = QVBoxLayout(self.drop_zone)
        self._drop_layout = drop_layout
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.setSpacing(16)

        # Vector icon label
        self.icon_label = IconLabel('music', IconColors.default(), 48)
        drop_layout.addWidget(self.icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Text label
        self.text_label = QLabel(tr("file_drop_title"))
        self.text_label.setProperty("role", "muted")
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_label.setWordWrap(True)
        drop_layout.addWidget(self.text_label)

        # Browse button
        self.browse_btn = self._i18n.text(QPushButton(), "file_browse", tooltip="tooltip_browse")
        self.browse_btn.setProperty("variant", "primary")
        self.browse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.browse_btn.clicked.connect(self._browse_files)
        drop_layout.addWidget(self.browse_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Formats hint
        self.formats_hint = self._i18n.text(QLabel(), "file_formats_hint")
        self.formats_hint.setProperty("role", "dim")
        self.formats_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.formats_hint.setWordWrap(True)
        drop_layout.addWidget(self.formats_hint)

        layout.addWidget(self.drop_zone)

        # Selected file info (hidden initially)
        self.file_info = QWidget()
        self.file_info.setVisible(False)
        self.file_info.setProperty("role", "accent-card")
        file_info_layout = QHBoxLayout(self.file_info)
        file_info_layout.setContentsMargins(12, 10, 12, 10)

        # File icon
        self.file_icon = IconLabel('file', IconColors.PRIMARY, 18)
        file_info_layout.addWidget(self.file_icon)

        self.file_name_label = ElidedLabel()
        self.file_name_label.setProperty("role", "heading")
        file_info_layout.addWidget(self.file_name_label, stretch=1)

        self.file_duration_label = QLabel()
        self.file_duration_label.setProperty("role", "muted")
        file_info_layout.addWidget(self.file_duration_label)

        self.clear_btn = QPushButton()
        self.clear_btn.setIcon(get_icon('close', IconColors.default(), 14))
        self.clear_btn.setFixedSize(24, 24)
        self.clear_btn.setProperty("role", "icon-button-danger")
        self._i18n.text(self.clear_btn, "file_clear", "setToolTip")
        self._i18n.text(self.clear_btn, "file_clear", "setAccessibleName")
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
                    set_role(self.drop_zone, "drop-zone-active")
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
        set_role(self.drop_zone, "drop-zone")

    def _browse_files(self):
        """Open file browser dialog."""
        formats = ' '.join(f'*{ext}' for ext in SUPPORTED_FORMATS)
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            tr("file_dialog_title"),
            "",
            tr("file_dialog_filter", formats=formats)
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

        self.file_duration_label.setText(tr("file_duration_checking"))
        self._start_duration_probe(filepath)

        # Show file info, update drop zone with success state
        self.file_info.setVisible(True)
        if self._compact:
            self.drop_zone.setVisible(False)
        self.icon_label.set_icon('check_circle')
        self.icon_label.set_color(IconColors.SUCCESS)
        self.text_label.setText(tr("file_ready"))
        set_role(self.text_label, "success-text")

        # Emit signal
        self.file_selected.emit(filepath)

    def _clear_selection(self):
        """Clear the current selection."""
        self.selected_file = None
        self.file_info.setVisible(False)
        self.drop_zone.setVisible(True)
        self._cancel_duration_probe()
        self.icon_label.set_icon('music')
        self.icon_label.set_color(IconColors.default())
        self.text_label.setText(tr("file_drop_title"))
        set_role(self.text_label, "muted")
        self.file_cleared.emit()

    def get_file(self) -> str | None:
        """Get the currently selected file path."""
        return self.selected_file

    def shutdown(self) -> None:
        """Stop an in-flight ffprobe without delaying window shutdown.
        Part of the Shutdownable protocol (ui/shutdownable.py)."""
        self._cancel_duration_probe()

    def _start_duration_probe(self, filepath: str) -> None:
        if self._duration_probe.state() != QProcess.ProcessState.NotRunning:
            self._probe_path = None
            self._pending_probe_path = filepath
            self._duration_probe.kill()
            return
        self._launch_duration_probe(filepath)

    def _launch_duration_probe(self, filepath: str) -> None:
        executable = resolve_tool("ffprobe")
        if not executable:
            self.file_duration_label.setText("")
            return
        self._probe_path = filepath
        self._duration_probe.start(executable, [
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            filepath,
        ])

    def _cancel_duration_probe(self) -> None:
        self._probe_path = None
        self._pending_probe_path = None
        if self._duration_probe.state() != QProcess.ProcessState.NotRunning:
            self._duration_probe.kill()

    def _on_duration_probe_finished(self, exit_code: int, _status) -> None:
        probed_path = self._probe_path
        self._probe_path = None
        if not probed_path or probed_path != self.selected_file or exit_code != 0:
            if probed_path == self.selected_file:
                self.file_duration_label.setText("")
        else:
            try:
                duration = float(
                    bytes(self._duration_probe.readAllStandardOutput()).decode().strip()
                )
            except (TypeError, ValueError):
                duration = 0.0
            self.file_duration_label.setText(
                format_duration(duration) if duration > 0 else ""
            )
        pending = self._pending_probe_path
        self._pending_probe_path = None
        if pending and pending == self.selected_file:
            self._launch_duration_probe(pending)

    def set_compact(self, compact: bool) -> None:
        """Keep the primary file action reachable in short windows.

        At the supported 900x550 minimum, the full illustration and format
        hint forced the browse button below the form's viewport.  Compact
        mode preserves the meaningful title and action while removing only
        decorative/help content already available in the file dialog.
        """
        self._compact = compact
        self.drop_zone.setVisible(not (compact and self.selected_file is not None))
        self.icon_label.setVisible(not compact)
        self.formats_hint.setVisible(not compact)
        self.text_label.setWordWrap(not compact)
        self._drop_layout.setSpacing(6 if compact else 16)
        margin = 8 if compact else 9
        self._drop_layout.setContentsMargins(margin, margin, margin, margin)
        if compact:
            # Theme rules give buttons a 34px minimum height. Pinning the
            # compact drop zone to the exact one-line layout prevents its
            # former 184px geometry from overflowing a 78px parent.
            self.drop_zone.setFixedHeight(72)
            self.setMinimumHeight(72)
        else:
            self.drop_zone.setMinimumHeight(0)
            self.drop_zone.setMaximumHeight(16_777_215)
            self.setMinimumHeight(0)
