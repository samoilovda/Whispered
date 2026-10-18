"""Manage collections (N03): create, rename, delete. A collection only
groups records — deleting one never deletes a record, and nothing is copied
or moved. Modal, rebuilt each time it opens."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.i18n import tr


class CollectionsDialog(QDialog):
    def __init__(self, store, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self.setWindowTitle(tr("collections_manage"))
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        note = QLabel(tr("collections_note"))
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        layout.addWidget(note)
        self.list = QListWidget()
        self.list.setAccessibleName(tr("collections_manage"))
        layout.addWidget(self.list)
        row = QHBoxLayout()
        for key, slot in (("collections_new", self._new), ("collections_rename", self._rename),
                          ("collections_delete", self._delete)):
            button = QPushButton(tr(key))
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._reload()

    def _reload(self) -> None:
        self._items = self._store.list_collections()
        self.list.clear()
        for item in self._items:
            self.list.addItem(f"{item['name']}  ({tr('collections_records', count=item['records'])})")

    def _current(self):
        row = self.list.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _ask(self, title_key: str, initial: str = "") -> str:
        text, ok = QInputDialog.getText(self, tr(title_key), tr("collections_name"),
                                        QLineEdit.EchoMode.Normal, initial)
        return text.strip() if ok else ""

    def _new(self) -> None:
        name = self._ask("collections_new")
        if name and self._store.create_collection(name) is None:
            QMessageBox.information(self, tr("collections_new"), tr("collections_name_taken"))
        self._reload()

    def _rename(self) -> None:
        current = self._current()
        if current is None:
            return
        name = self._ask("collections_rename", current["name"])
        if name and name != current["name"] and not self._store.rename_collection(current["id"], name):
            QMessageBox.information(self, tr("collections_rename"), tr("collections_name_taken"))
        self._reload()

    def _delete(self) -> None:
        current = self._current()
        if current is None:
            return
        reply = QMessageBox.question(
            self, tr("collections_delete"),
            tr("collections_delete_confirm", name=current["name"], count=current["records"]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._store.delete_collection(current["id"])
            self._reload()
