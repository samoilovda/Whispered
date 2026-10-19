"""Two-column workspace shell with a persistent Library.

Was three columns (Library | Document | Inspector) — the inspector rail
is retired in B6 (see docs/UI_REDESIGN_PLAN_2026-09.ru.md): every
destination it used to host is now either a StartView/RunView screen or
a tab on RecordView's own content widget, so there's nothing left for a
third column to hold.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QBoxLayout,
    QToolButton,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from config import get_config, save_config
from core.i18n import tr
from ui.i18n_helpers import Retranslator
from ui.icons import IconColors, get_icon


LIBRARY_COMPACT_WIDTH = 54
LIBRARY_WIDTH = 280
# MainWindow.setMinimumSize(900, 550) means the window itself can never
# get narrower than 900 — keep this comfortably above that floor so the
# forced-compact rule stays reachable at the window's own minimum size,
# instead of being dead code below a width nothing can actually resize to.
FORCE_COMPACT_WIDTH = 960


class WorkspaceShell(QWidget):
    """Own the Library | center geometry and resize rules."""

    new_requested = pyqtSignal()
    palette_requested = pyqtSignal()
    queue_requested = pyqtSignal()

    def __init__(self, library: QWidget, center: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self.center = center
        self._forced_compact = False
        # The Library's width as the user dragged it (Config.library_width).
        saved = int(getattr(get_config(), "library_width", 0) or 0)
        self._library_width = saved if 220 <= saved <= 420 else LIBRARY_WIDTH
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.call(self._retranslate_shell)
        self._i18n.bind()

    def _retranslate_shell(self) -> None:
        self.library_title.setText(tr("workspace_records"))
        self.new_button.setAccessibleName(tr("workspace_new"))
        self.library_button.setToolTip(tr("workspace_open_library"))
        self.library_button.setAccessibleName(tr("workspace_open_library"))
        self.search_button.setToolTip(tr("shortcut_palette"))
        self.search_button.setAccessibleName(tr("shortcut_palette"))
        self.queue_button.setToolTip(tr("menu_show_queue"))
        self.queue_button.setAccessibleName(tr("menu_show_queue"))
        self._apply_responsiveness()

    def _setup_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(1)

        self.library_pane = QWidget()
        self.library_pane.setProperty("role", "sidebar-pane")
        library_layout = QVBoxLayout(self.library_pane)
        library_layout.setContentsMargins(12, 12, 12, 12)
        library_layout.setSpacing(8)
        header = QHBoxLayout()
        self._header = header
        self.library_title = QLabel(tr("workspace_records"))
        self.library_title.setProperty("role", "section-title")
        header.addWidget(self.library_title, stretch=1)
        # Compact mode: the Library and "New record" are two buttons with
        # their own names (UX-10) — one button used to do either, while
        # its accessible name always said "New record".
        self.library_button = QToolButton()
        self.library_button.setProperty("role", "toolbar-icon")
        self.library_button.setIcon(get_icon("list", IconColors.default(), 16))
        self.library_button.setToolTip(tr("workspace_open_library"))
        self.library_button.setAccessibleName(tr("workspace_open_library"))
        self.library_button.clicked.connect(self.show_library)
        self.library_button.setVisible(False)
        header.addWidget(self.library_button)
        self.new_button = QPushButton(tr("workspace_new"))
        self.new_button.setProperty("variant", "primary")
        self.new_button.setAccessibleName(tr("workspace_new"))
        self.new_button.clicked.connect(self._on_header_action)
        header.addWidget(self.new_button)
        # Mini-Rail quick actions in compact mode: Command Palette (⌘K) and Queue.
        self.search_button = QToolButton()
        self.search_button.setProperty("role", "toolbar-icon")
        self.search_button.setIcon(get_icon("search", IconColors.default(), 16))
        self.search_button.setToolTip(tr("shortcut_palette"))
        self.search_button.setAccessibleName(tr("shortcut_palette"))
        self.search_button.clicked.connect(self.palette_requested.emit)
        self.search_button.setVisible(False)
        header.addWidget(self.search_button)

        self.queue_button = QToolButton()
        self.queue_button.setProperty("role", "toolbar-icon")
        self.queue_button.setIcon(get_icon("layers", IconColors.default(), 16))
        self.queue_button.setToolTip(tr("menu_show_queue"))
        self.queue_button.setAccessibleName(tr("menu_show_queue"))
        self.queue_button.clicked.connect(self.queue_requested.emit)
        self.queue_button.setVisible(False)
        header.addWidget(self.queue_button)
        library_layout.addLayout(header)
        library_layout.addWidget(self.library, stretch=1)
        library_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.splitter.addWidget(self.library_pane)
        self.splitter.addWidget(self.center)
        self.center.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.center.setMinimumWidth(360)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        root.addWidget(self.splitter)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_responsiveness()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsiveness()

    def _apply_responsiveness(self) -> None:
        forced = self.width() < FORCE_COMPACT_WIDTH
        cfg = get_config()
        compact_library = forced or getattr(cfg, "library_collapsed", False)
        self._forced_compact = forced
        self._compact_library = compact_library
        self.library.setVisible(not compact_library)
        self.library_title.setVisible(not compact_library)
        self.new_button.setText("" if compact_library else tr("workspace_new"))
        self.new_button.setIcon(
            get_icon("plus", IconColors.default(), 16) if compact_library else QIcon()
        )
        self.new_button.setToolTip(tr("workspace_new"))
        self.library_button.setVisible(compact_library)
        self.search_button.setVisible(compact_library)
        self.queue_button.setVisible(compact_library)
        # The compact column is one icon wide: stack its buttons vertically.
        self._header.setDirection(
            QBoxLayout.Direction.TopToBottom if compact_library
            else QBoxLayout.Direction.LeftToRight
        )
        self.library_pane.setMinimumWidth(
            LIBRARY_COMPACT_WIDTH if compact_library else 220
        )
        self.library_pane.setMaximumWidth(
            LIBRARY_COMPACT_WIDTH if compact_library else 420
        )
        if self.width() > 0:
            library_width = LIBRARY_COMPACT_WIDTH if compact_library else self._library_width
            self.splitter.setSizes(
                [library_width, max(360, self.width() - library_width)]
            )

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        """Keep the width the user dragged the Library to; resizing the
        window used to snap it back to the default every time."""
        if getattr(self, "_compact_library", False):
            return
        width = self.splitter.sizes()[0]
        if 220 <= width <= 420:
            self._library_width = width

    def library_width(self) -> int:
        return self._library_width

    def _on_header_action(self) -> None:
        self.new_requested.emit()

    def show_library(self) -> None:
        """Open the Library in a compact window (until the next resize),
        without changing the saved collapsed setting."""
        if not self.library.isVisible():
            self.library.setVisible(True)
            self.library_title.setVisible(True)
            self.library_button.setVisible(False)
            self.search_button.setVisible(False)
            self.queue_button.setVisible(False)
            self.library_pane.setMinimumWidth(220)
            self.library_pane.setMaximumWidth(420)
            width = self._library_width
            self.splitter.setSizes([width, max(360, self.width() - width)])

    def set_library_collapsed(self, collapsed: bool) -> None:
        cfg = get_config()
        cfg.library_collapsed = bool(collapsed)
        save_config()
        self._apply_responsiveness()
