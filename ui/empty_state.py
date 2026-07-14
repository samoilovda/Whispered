from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from ui.icons import IconColors, IconLabel

class EmptyStateWidget(QWidget):
    """A centered empty state with an icon, a message, and an optional
    smaller hint line underneath (e.g. Library-without-records: a title
    plus a one-line nudge toward the action that would fill it)."""
    def __init__(self, icon_name: str, message: str, hint: str = "", parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)

        # Center container
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icon
        self.icon_label = IconLabel(icon_name, IconColors.MUTED, 48)
        container_layout.addWidget(self.icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        container_layout.addSpacing(16)

        # Message
        self.message_label = QLabel(message)
        self.message_label.setProperty("role", "muted")
        self.message_label.setStyleSheet("font-size: 14px;")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setWordWrap(True)
        container_layout.addWidget(self.message_label, alignment=Qt.AlignmentFlag.AlignCenter)

        if hint:
            container_layout.addSpacing(4)
            self.hint_label = QLabel(hint)
            self.hint_label.setProperty("role", "dim")
            self.hint_label.setStyleSheet("font-size: 12px;")
            self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.hint_label.setWordWrap(True)
            container_layout.addWidget(self.hint_label, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignCenter)
