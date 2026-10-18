"""The preview step of exporting / importing a project (N06): what will be
written or added is shown first, and nothing happens until the user agrees.
Modal, rebuilt each time it opens."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from ui.qt_util import must


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


class ProjectPreviewDialog(QDialog):
    def __init__(
        self, title: str, heading: str, lines: list[str], ok_text: str,
        *, media_text: str = "", media_enabled: bool = True, parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        head = QLabel(heading)
        head.setWordWrap(True)
        head.setProperty("role", "section-title")
        layout.addWidget(head)
        body = QLabel("\n".join(f"• {line}" for line in lines))
        body.setWordWrap(True)
        layout.addWidget(body)
        self.media_check: Optional[QCheckBox] = None
        if media_text:
            self.media_check = QCheckBox(media_text)
            self.media_check.setEnabled(media_enabled)
            layout.addWidget(self.media_check)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        must(buttons.button(QDialogButtonBox.StandardButton.Ok)).setText(ok_text)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def include_media(self) -> bool:
        return self.media_check is not None and self.media_check.isChecked()
