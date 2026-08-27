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

    def __init__(self, library: QWidget, center: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self.center = center
        self._forced_compact = False
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.call(self._retranslate_shell)
        self._i18n.bind()

    def _retranslate_shell(self) -> None:
        self.library_title.setText(tr("workspace_records"))
        self.new_button.setAccessibleName(tr("workspace_new"))
        self._apply_responsiveness()

    def _setup_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(1)

        self.library_pane = QWidget()
        library_layout = QVBoxLayout(self.library_pane)
        library_layout.setContentsMargins(12, 12, 12, 12)
        library_layout.setSpacing(8)
        header = QHBoxLayout()
        self.library_title = QLabel(tr("workspace_records"))
        self.library_title.setProperty("role", "section-title")
        header.addWidget(self.library_title, stretch=1)
        self.new_button = QPushButton(tr("workspace_new"))
        self.new_button.setProperty("variant", "primary")
        self.new_button.setAccessibleName(tr("workspace_new"))
        self.new_button.clicked.connect(self._on_header_action)
        header.addWidget(self.new_button)
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
            get_icon("file", IconColors.default(), 16) if compact_library else QIcon()
        )
        self.new_button.setToolTip(
            tr("workspace_open_library") if compact_library else tr("workspace_new")
        )
        self.library_pane.setMinimumWidth(
            LIBRARY_COMPACT_WIDTH if compact_library else 220
        )
        self.library_pane.setMaximumWidth(
            LIBRARY_COMPACT_WIDTH if compact_library else 420
        )
        if self.width() > 0:
            library_width = LIBRARY_COMPACT_WIDTH if compact_library else LIBRARY_WIDTH
            self.splitter.setSizes(
                [library_width, max(360, self.width() - library_width)]
            )

    def _on_header_action(self) -> None:
        if getattr(self, "_compact_library", False) and not self.library.isVisible():
            self.library.setVisible(True)
            self.library_title.setVisible(True)
            self.library_pane.setMinimumWidth(220)
            self.library_pane.setMaximumWidth(420)
            self.splitter.setSizes([280, max(360, self.width() - 280)])
            return
        self.new_requested.emit()

    def set_library_collapsed(self, collapsed: bool) -> None:
        cfg = get_config()
        cfg.library_collapsed = bool(collapsed)
        save_config()
        self._apply_responsiveness()
