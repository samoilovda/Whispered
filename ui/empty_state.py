from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt
from ui.icons import IconColors, IconLabel

class EmptyStateWidget(QWidget):
    """A centered empty state with an icon, a message, and an optional
    smaller hint line underneath (e.g. Library-without-records: a title
    plus a one-line nudge toward the action that would fill it)."""

    _ICON_SIZE_FULL = 48
    _ICON_SIZE_COMPACT = 28
    # Below this height the full stack (icon + spacing + title + hint +
    # spacing + button) no longer fits whatever's left after everything
    # else on the page (e.g. Library's drop zone) and starts overlapping —
    # QVBoxLayout doesn't clip gracefully when its children's combined
    # minimum size exceeds the space it's given, it lets them overlap.
    _COMPACT_HEIGHT = 220

    def __init__(
        self,
        icon_name: str,
        message: str,
        hint: str = "",
        action_text: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self._compact = False

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 0)

        # Center container
        container = QWidget()
        self._container_layout = container_layout = QVBoxLayout(container)
        container_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Icon
        self.icon_label = IconLabel(icon_name, IconColors.muted(), self._ICON_SIZE_FULL)
        container_layout.addWidget(self.icon_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self._icon_spacer_index = container_layout.count()
        container_layout.addSpacing(16)

        # Message
        self.message_label = QLabel(message)
        self.message_label.setProperty("role", "section-title")
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message_label.setWordWrap(True)
        container_layout.addWidget(self.message_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.hint_label = None
        if hint:
            container_layout.addSpacing(4)
            self.hint_label = QLabel(hint)
            self.hint_label.setProperty("role", "dim")
            self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.hint_label.setWordWrap(True)
            container_layout.addWidget(self.hint_label, alignment=Qt.AlignmentFlag.AlignCenter)

        self.action_button = QPushButton(action_text)
        self.action_button.setProperty("variant", "primary")
        self.action_button.setVisible(bool(action_text))
        container_layout.addSpacing(12)
        container_layout.addWidget(
            self.action_button, alignment=Qt.AlignmentFlag.AlignCenter
        )

        layout.addWidget(container, alignment=Qt.AlignmentFlag.AlignCenter)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._set_compact(self.height() < self._COMPACT_HEIGHT)

    def _set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.icon_label.set_size(
            self._ICON_SIZE_COMPACT if compact else self._ICON_SIZE_FULL
        )
        spacer = self._container_layout.itemAt(self._icon_spacer_index)
        if spacer is not None:
            spacer.spacerItem().changeSize(0, 4 if compact else 16)
        if self.hint_label is not None:
            self.hint_label.setVisible(not compact)
        self._container_layout.activate()
