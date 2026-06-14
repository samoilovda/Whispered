from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from ui.icons import get_icon, IconColors, IconLabel

class EmptyStateWidget(QWidget):
    """A centered empty state with an icon and a message."""
    def __init__(self, icon_name: str, message: str, parent=None):
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
        self.message_label.setStyleSheet("color: #888; font-size: 14px;")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setWordWrap(True)
        container_layout.addWidget(self.message_label, alignment=Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignCenter)
