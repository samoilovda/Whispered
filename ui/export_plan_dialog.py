"""Preview of an export bundle before anything is written (UI/UX audit
2026-10-18, UX-15 / T15): every file that will be written, how fresh each
material is, what is missing, and which files already exist in the folder.
Modal and rebuilt on each open, like the other .exec() dialogs."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

from application.export_plan import CURRENT, MISSING, STALE, UNKNOWN, ExportPlan, summarize
from core.i18n import tr
from ui.qt_util import must

_GLYPH = {CURRENT: "✓", STALE: "◐", UNKNOWN: "?", MISSING: "—"}


def item_text(item) -> str:
    """One preview line: glyph, file, and what is notable about it."""
    if item.freshness == MISSING:
        return f"{_GLYPH[MISSING]} {tr('export_plan_missing', name=item.key)}"
    notes = []
    if item.kind == "material" and item.freshness == STALE:
        notes.append(tr("export_plan_stale"))
    elif item.kind == "material" and item.freshness == UNKNOWN:
        notes.append(tr("export_plan_unknown"))
    if item.user_edits:
        notes.append(tr("export_plan_edits", keys=", ".join(item.user_edits)))
    if item.same_file:
        notes.append(tr("export_plan_same_file"))
    elif item.exists:
        notes.append(tr("export_plan_exists"))
    suffix = f" — {'; '.join(notes)}" if notes else ""
    return f"{_GLYPH[item.freshness]} {item.target}{suffix}"


class ExportPlanDialog(QDialog):
    def __init__(self, plan: ExportPlan, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("export_plan_title"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        counts = summarize(plan)
        header = QLabel(tr("export_plan_header", count=counts["write"], dir=plan.directory))
        header.setWordWrap(True)
        layout.addWidget(header)
        self.items = QListWidget()
        for item in plan.items:
            row = QListWidgetItem(item_text(item))
            row.setToolTip(item.source or item.target)
            self.items.addItem(row)
        layout.addWidget(self.items)
        if counts[STALE]:
            stale = QLabel(tr("export_plan_stale_note"))
            stale.setProperty("role", "warning-text")
            stale.setWordWrap(True)
            layout.addWidget(stale)
        self.overwrite = QCheckBox(tr("export_plan_overwrite", count=counts["conflicts"]))
        self.overwrite.setVisible(bool(counts["conflicts"]))
        layout.addWidget(self.overwrite)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        must(buttons.button(QDialogButtonBox.StandardButton.Ok)).setText(tr("export_plan_go"))
        must(buttons.button(QDialogButtonBox.StandardButton.Ok)).setEnabled(counts["write"] > 0)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
