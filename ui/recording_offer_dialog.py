"""The "new recording finished" offer: names the recording, lets the user
pick a recipe and run it — or dismiss the offer. Nothing starts without
the click.

Non-modal so it never interrupts typing elsewhere in the window. Like a
toast it is transient: it is built in the active language and not
retranslated while open.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget,
)

from core.i18n import tr
from domain.recipe import Recipe
from ui.option_labels import recipe_label


def _recording_name(path: str) -> str:
    """The meeting folder's name for a file inside a recording sub-folder
    (Zoom's ``2026-10-27 10.00.00 Meeting``), else the file name."""
    media = Path(path)
    return media.parent.name if media.stem.lower().startswith(("audio", "video")) else media.name


class RecordingOfferDialog(QDialog):
    """Emits ``run_requested(path, recipe_key)`` when the user accepts."""

    run_requested = pyqtSignal(str, str)

    def __init__(
        self,
        path: str,
        recipes: "list[tuple[str, Recipe]]",
        selected_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self.setWindowTitle(tr("recording_offer_title"))
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        message = QLabel(tr("recording_offer_message", name=_recording_name(path)))
        message.setWordWrap(True)
        layout.addWidget(message)

        file_label = QLabel(path)
        file_label.setProperty("role", "muted")
        file_label.setProperty("size", "small")
        file_label.setWordWrap(True)
        file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(file_label)

        layout.addWidget(QLabel(tr("recording_offer_recipe")))
        self.recipe_combo = QComboBox()
        for key, recipe in recipes:
            self.recipe_combo.addItem(recipe_label(recipe), key)
        index = self.recipe_combo.findData(selected_key)
        self.recipe_combo.setCurrentIndex(index if index >= 0 else 0)
        layout.addWidget(self.recipe_combo)

        buttons = QDialogButtonBox()
        run_btn = buttons.addButton(tr("recording_offer_run"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("recording_offer_later"), QDialogButtonBox.ButtonRole.RejectRole)
        if run_btn is not None:
            run_btn.setDefault(True)
        buttons.accepted.connect(self._on_run)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def path(self) -> str:
        return self._path

    def _on_run(self) -> None:
        key = self.recipe_combo.currentData()
        self.accept()
        if key:
            self.run_requested.emit(self._path, str(key))
