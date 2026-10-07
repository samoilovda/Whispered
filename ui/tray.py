"""Menu-bar / system-tray icon (T, docs/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md).

A small menu for getting to work without hunting for the window: start
or stop the recorder, open the live session, open a file, jump to a
recent record — and a line saying what is being processed right now.

It only mirrors what the window already shows: no queue or state of its
own. The window's actions do the work; this module calls them. Global
hotkeys and meeting reminders are separate decisions and not here.
"""

from __future__ import annotations

import platform
from typing import Callable, Optional

from PyQt6.QtCore import QObject
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon

from core.i18n import tr
from core.logger import get_logger

logger = get_logger(__name__)

_RECENT = 5


class TrayController(QObject):
    """Owns the tray icon and rebuilds its menu each time it opens, so
    the recent records and the status line are always current."""

    def __init__(
        self,
        window,
        *,
        status_text: Callable[[], str],
        recording: Callable[[], bool],
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent or window)
        self._window = window
        self._status_text = status_text
        self._recording = recording
        self._icon = QSystemTrayIcon(self)
        self._icon.setIcon(_tray_icon())
        self._icon.setToolTip("Whispered")
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._rebuild)
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)
        self._rebuild()

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> None:
        self._icon.show()

    def hide(self) -> None:
        self._icon.hide()

    def menu(self) -> QMenu:
        return self._menu

    def _on_activated(self, reason) -> None:
        # A click on Windows/Linux shows the window; macOS always opens
        # the menu, which has "Show Whispered" first.
        if reason == QSystemTrayIcon.ActivationReason.Trigger and platform.system() != "Darwin":
            self._show_window()

    def _show_window(self) -> None:
        window = self._window
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    def _add(self, text: str, slot, enabled: bool = True) -> QAction:
        action = self._menu.addAction(text)
        action.setEnabled(enabled)
        if slot is not None:
            action.triggered.connect(slot)
        return action

    def _rebuild(self) -> None:
        menu = self._menu
        menu.clear()
        window = self._window

        status = (self._status_text() or "").strip()
        if status:
            self._add(status, None, enabled=False)
            menu.addSeparator()

        self._add(tr("tray_show_window"), self._show_window)
        menu.addSeparator()
        self._add(
            tr("tray_stop_recording") if self._recording() else tr("tray_start_recording"),
            lambda: self._then_show(window._menu_toggle_recording),
        )
        self._add(tr("tray_live"), lambda: self._then_show(lambda: window._on_section_changed("live")))
        self._add(tr("tray_open_file"), lambda: self._then_show(window._menu_open_file))

        recent = menu.addMenu(tr("tray_recent"))
        try:
            from core.history import get_history_store
            from ui.library_view import display_name

            records = get_history_store().list(limit=_RECENT)
        except Exception as exc:  # noqa: BLE001 - history may be disabled
            logger.debug("Tray: no recent records: %s", exc)
            records = []
        if not records:
            empty = recent.addAction(tr("tray_no_records"))
            empty.setEnabled(False)
        for record in records:
            name = display_name(getattr(record, "title", "") or record.source_name)
            action = recent.addAction(name)
            action.triggered.connect(
                lambda _c=False, rid=record.id: self._then_show(lambda: window._open_record_view(rid))
            )

        menu.addSeparator()
        self._add(tr("tray_quit"), window.close)

    def _then_show(self, action) -> None:
        self._show_window()
        action()


def _tray_icon() -> QIcon:
    """A monochrome microphone the system recolours for the menu bar
    (a "template" image on macOS)."""
    from ui.icons import get_icon

    icon = get_icon("microphone", "#000000", 18)
    icon.setIsMask(True)
    return icon
