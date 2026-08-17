"""Manual frame/tile picker placeholder used by the cover workspace."""

from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout

from core.i18n import tr


class FramePicker(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("cover_frame_picker"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("cover_frame_picker_hint")))
