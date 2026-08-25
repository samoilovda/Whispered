"""Small reusable production UI building blocks.

The widgets in this module own presentation only. Business state remains in
MainWindow/controllers and is passed in through narrow setters or signals.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionComboBox,
    QStylePainter,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QGraphicsDropShadowEffect,
)
from PyQt6.QtGui import QColor, QFontMetrics

from ui.theme import SPACE_1, SPACE_2, SPACE_3, SPACE_4, SPACE_6, mark_elides, set_role


class ElidingComboBox(QComboBox):
    """QComboBox whose closed-state label elides with an ellipsis instead
    of being hard-clipped when the box is narrower than its current item's
    text (Qt's own style-level eliding does not survive this project's QSS
    styling — see docs/UI_REDESIGN_PLAN_2026-09.ru.md, A3). Dropdown items
    and ``currentText()``/``currentData()`` are unaffected; only what gets
    painted in the closed box is shortened. Always opts itself out of the
    UI gallery's clipped-text check via ``ui.theme.mark_elides``; when
    *sync_tooltip* is true (the default) it also keeps the tooltip in sync
    with the full current text — pass false for a combo box that already
    carries its own more useful static tooltip (e.g. one explaining what
    each choice means, not just repeating its label).
    """

    def __init__(self, parent: QWidget | None = None, *, sync_tooltip: bool = True) -> None:
        super().__init__(parent)
        self.setProperty("_elides", True)
        self._sync_tooltip_enabled = sync_tooltip
        if sync_tooltip:
            self.currentIndexChanged.connect(self._sync_tooltip)

    def _sync_tooltip(self, _index: int = -1) -> None:
        mark_elides(self, self.currentText())

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._sync_tooltip_enabled:
            self._sync_tooltip()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QStylePainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)

        text_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            opt,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        metrics = QFontMetrics(self.font())
        opt.currentText = metrics.elidedText(
            opt.currentText, Qt.TextElideMode.ElideRight, text_rect.width()
        )
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)


class FlowLayout(QLayout):
    """Left-to-right layout that wraps to a new row instead of squeezing
    its items narrower than their own ``sizeHint()``.

    A plain ``QHBoxLayout`` shrinks every child below its size hint once
    the row runs out of horizontal space — that's how a filter chip like
    "Диктофон" ends up rendered as "iктоф" (see
    docs/UI_REDESIGN_PLAN_2026-09.ru.md, A2). This is the standard
    Qt "flow layout" pattern (a QLayout, not a positioned QWidget) so it
    participates normally in the parent's size negotiation via
    heightForWidth.
    """

    def __init__(self, parent=None, margin: int = 0, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        if margin >= 0:
            self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item) -> None:  # noqa: N802
        self._items.append(item)

    def horizontalSpacing(self) -> int:  # noqa: N802
        return self._spacing

    def verticalSpacing(self) -> int:  # noqa: N802
        return self._spacing

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )
        return size

    def _do_layout(self, rect, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x, y = effective.x(), effective.y()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + self._spacing
            if next_x - self._spacing > effective.right() and line_height > 0:
                x = effective.x()
                y += line_height + self._spacing
                next_x = x + hint.width() + self._spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class PageHeader(QWidget):
    """Consistent page title, optional subtitle and contextual actions."""

    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_4)

        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(SPACE_1)
        self.title_label = QLabel(title)
        self.title_label.setProperty("role", "page-title")
        self.title_label.setAccessibleName(title)
        copy.addWidget(self.title_label)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setProperty("role", "muted")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        copy.addWidget(self.subtitle_label)
        layout.addLayout(copy, stretch=1)

        self.actions = QHBoxLayout()
        self.actions.setContentsMargins(0, 0, 0, 0)
        self.actions.setSpacing(SPACE_2)
        layout.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)
        self.title_label.setAccessibleName(title)

    def set_subtitle(self, subtitle: str) -> None:
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle))


class StatusBadge(QLabel):
    """Textual status chip. Semantic colour is never its only signal."""

    _MARKERS = {
        "neutral": "—",
        "info": "i",
        "success": "✓",
        "warning": "!",
        "error": "×",
    }

    def __init__(self, text: str = "", state: str = "neutral", parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_status(text, state)

    def set_status(self, text: str, state: str = "neutral") -> None:
        state = state if state in self._MARKERS else "neutral"
        self.setText(f"{self._MARKERS[state]}  {text}" if text else "")
        self.setAccessibleName(text)
        set_role(self, f"status-{state}")
        self.setVisible(bool(text))


class InlineBanner(QWidget):
    """Non-modal message with an optional corrective action."""

    action_requested = pyqtSignal()

    def __init__(
        self,
        text: str = "",
        *,
        severity: str = "info",
        action_text: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_3, SPACE_2, SPACE_3, SPACE_2)
        layout.setSpacing(SPACE_3)
        self.message_label = QLabel(text)
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label, stretch=1)
        self.action_button = QPushButton(action_text)
        self.action_button.setProperty("variant", "ghost")
        self.action_button.clicked.connect(self.action_requested.emit)
        self.action_button.setVisible(bool(action_text))
        layout.addWidget(self.action_button)
        self.set_message(text, severity=severity, action_text=action_text)

    def set_message(
        self,
        text: str,
        *,
        severity: str = "info",
        action_text: str | None = None,
    ) -> None:
        severity = severity if severity in {"info", "warning", "error", "success"} else "info"
        self.message_label.setText(text)
        set_role(self, "inline-banner" if severity == "info" else f"inline-banner-{severity}")
        if action_text is not None:
            self.action_button.setText(action_text)
            self.action_button.setVisible(bool(action_text))
        self.setVisible(bool(text))


class CollapsiblePanel(QWidget):
    """A keyboard-accessible disclosure widget with a one-line summary."""

    expanded_changed = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        *,
        summary: str = "",
        expanded: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACE_2)
        header = QHBoxLayout()
        self.toggle = QToolButton()
        self.toggle.setProperty("role", "collapsible-header")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.toggle.setText(title)
        self.toggle.setAccessibleName(title)
        self.toggle.toggled.connect(self._on_toggled)
        header.addWidget(self.toggle)
        self.summary_label = QLabel(summary)
        self.summary_label.setProperty("role", "muted")
        header.addWidget(self.summary_label, stretch=1)
        root.addLayout(header)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(SPACE_4, 0, 0, SPACE_2)
        self.body_layout.setSpacing(SPACE_3)
        self.body.setVisible(expanded)
        root.addWidget(self.body)

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def set_summary(self, summary: str) -> None:
        self.summary_label.setText(summary)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(expanded)

    def is_expanded(self) -> bool:
        return self.toggle.isChecked()

    def _on_toggled(self, expanded: bool) -> None:
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.body.setVisible(expanded)
        self.expanded_changed.emit(expanded)


class FormSection(QWidget):
    """Compact card used to group related settings or setup controls."""

    def __init__(self, title: str = "", description: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "form-section")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        self.layout.setSpacing(SPACE_3)
        if title:
            label = QLabel(title)
            label.setProperty("role", "section-title")
            self.layout.addWidget(label)
        if description:
            label = QLabel(description)
            label.setProperty("role", "muted")
            label.setWordWrap(True)
            self.layout.addWidget(label)

    def add_widget(self, widget: QWidget) -> None:
        self.layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.layout.addLayout(layout)


class OperationBar(QFrame):
    """Shared long-running operation status; hidden in the idle state."""

    cancel_requested = pyqtSignal()
    details_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("role", "operation-bar")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        layout = QHBoxLayout()
        layout.setContentsMargins(SPACE_6, SPACE_3, SPACE_6, SPACE_3)
        layout.setSpacing(SPACE_3)
        self.status_label = QLabel()
        self.status_label.setProperty("role", "muted")
        layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(420)
        layout.addWidget(self.progress, stretch=1)
        self.details_button = QPushButton()
        self.details_button.setProperty("variant", "ghost")
        self.details_button.clicked.connect(self.details_requested.emit)
        self.details_button.setVisible(False)
        layout.addWidget(self.details_button)
        self.cancel_button = QPushButton()
        self.cancel_button.setProperty("variant", "danger")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        layout.addWidget(self.cancel_button)
        root.addLayout(layout)
        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(SPACE_6, 0, SPACE_6, SPACE_3)
        self.detail_layout.setSpacing(SPACE_2)
        root.addWidget(self.detail_container)
        self.setVisible(False)

    def add_detail_widget(self, widget: QWidget) -> None:
        self.detail_layout.addWidget(widget)

    def set_operation(
        self,
        text: str,
        *,
        progress: int | None = None,
        cancel_text: str = "",
        details_text: str = "",
    ) -> None:
        self.status_label.setText(text)
        if progress is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(max(0, min(100, progress)))
        self.cancel_button.setText(cancel_text)
        self.cancel_button.setVisible(bool(cancel_text))
        self.details_button.setText(details_text)
        self.details_button.setVisible(bool(details_text))
        self.setVisible(bool(text))

    def clear(self) -> None:
        self.status_label.clear()
        self.setVisible(False)


def apply_soft_shadow(widget: QWidget) -> None:
    """Applies a soft, modern drop shadow to a widget (Gemini style)."""
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(24)
    shadow.setXOffset(0)
    shadow.setYOffset(8)
    shadow.setColor(QColor(0, 0, 0, 15))
    widget.setGraphicsEffect(shadow)
