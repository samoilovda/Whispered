"""Explicit post-transcription chain checklist."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from config import get_config, save_config
from core.i18n import tr


_STEPS = (
    ("transcript", "chain_step_transcript"),
    ("youtube", "chain_step_youtube"),
    ("article", "chain_step_article"),
    ("insights", "chain_step_insights"),
    ("book", "chain_step_book"),
)


class StepChecklist(QWidget):
    """Persisted, readable replacement for the opaque preset combo."""

    changed = pyqtSignal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title = QLabel(tr("chain_steps_title"))
        title.setProperty("role", "section-title")
        layout.addWidget(title)
        hint = QLabel(tr("chain_steps_hint"))
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        selected = set(getattr(get_config(), "chain_steps", None) or ["transcript"])
        self._checks: dict[str, QCheckBox] = {}
        for key, label_key in _STEPS:
            check = QCheckBox(tr(label_key))
            check.setChecked(key in selected)
            if key == "transcript":
                check.setChecked(True)
                check.setEnabled(False)
            check.toggled.connect(self._save)
            self._checks[key] = check
            layout.addWidget(check)
        layout.addStretch()

    def selected_steps(self) -> list[str]:
        return [key for key, _label in _STEPS if self._checks[key].isChecked()]

    def _save(self, _checked: bool = False) -> None:
        steps = self.selected_steps()
        cfg = get_config()
        cfg.chain_steps = steps
        save_config()
        self.changed.emit(steps)

    def legacy_preset(self) -> str:
        """Map the checklist onto the existing sequential controller."""
        steps = set(self.selected_steps())
        if {"youtube", "article"}.issubset(steps):
            return "full"
        if "youtube" in steps:
            return "youtube"
        if "article" in steps:
            return "article"
        return "transcribe_only"
