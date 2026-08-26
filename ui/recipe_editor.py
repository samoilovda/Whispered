""""Настроить…"/"Изменить" — the escape hatch from StartView's five
built-in recipe chips (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B6).

Deliberately narrow: edits step selection (with dependency edges kept
consistent — checking "article" pulls in "clean", unchecking "clean"
drops anything that needed it) plus the existing transcription options
widget (model/language/mode/diarization), and saves the result as the
single custom recipe slot (``Config.recipes``, one entry) rather than a
full named-recipe library. A richer custom-recipe manager (multiple
saved recipes, renaming, deleting) is out of scope here — nothing in the
plan's B6 acceptance criteria calls for one, and Config.recipes was
already shaped as a list in B2 for exactly this kind of narrow start.

The name field (docs/IMPROVEMENT_PLAN_2026-08.ru.md, A4) exists even
within that one-slot scope: without it, saving from this dialog silently
discarded whichever built-in the user started from — "Сохранить" on
"Article from podcast" with no actual change made replaced
Config.last_recipe with an unnamed "custom" and unchecked every chip.
The caller (MainWindow._open_recipe_editor) now compares selected_steps()
against the recipe's original steps and only writes Config.recipes when
they actually differ.

The caller (MainWindow) owns ``transcribe_options`` — this dialog only
borrows it for the duration of ``exec()``. The caller must reparent it
back out (``transcribe_options.setParent(None)``) before discarding the
dialog: a QDialog owns its child widgets, and letting the dialog get
garbage-collected with transcribe_options still parented to it would
destroy that shared, otherwise-persistent widget along with it.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from application.steps import STEP_DEFINITIONS
from core.i18n import tr
from domain.recipe import Recipe

_STEP_BY_NAME = {step.name: step for step in STEP_DEFINITIONS}

# name -> the other step names that depend on it, i.e. must also be
# unchecked if this one is. Built once from the same registry
# application/steps.py already exposes, not duplicated by hand.
_DEPENDENTS: dict[str, tuple[str, ...]] = {
    step.name: tuple(
        other.name for other in STEP_DEFINITIONS if step.name in other.depends_on
    )
    for step in STEP_DEFINITIONS
}


class RecipeEditorDialog(QDialog):
    def __init__(self, transcribe_options: QWidget, recipe: Recipe, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("recipe_editor_title"))
        self.resize(420, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        name_label = QLabel(tr("recipe_editor_name_label"))
        name_label.setProperty("role", "section-title")
        root.addWidget(name_label)
        self._default_recipe_name = self._default_name(recipe)
        self._name_edit = QLineEdit()
        self._name_edit.setText(self._default_recipe_name)
        root.addWidget(self._name_edit)
        save_hint = QLabel(tr("recipe_editor_saves_as_copy"))
        save_hint.setProperty("role", "muted")
        save_hint.setWordWrap(True)
        root.addWidget(save_hint)

        transcription_label = QLabel(tr("recipe_editor_transcription_label"))
        transcription_label.setProperty("role", "section-title")
        root.addWidget(transcription_label)
        root.addWidget(transcribe_options)

        steps_label = QLabel(tr("recipe_editor_steps_label"))
        steps_label.setProperty("role", "section-title")
        root.addWidget(steps_label)

        selected = set(recipe.steps) | {"transcribe"}
        self._checks: dict[str, QCheckBox] = {}
        for step in STEP_DEFINITIONS:
            check = QCheckBox(tr(step.label_key))
            check.setChecked(step.name in selected)
            if step.name == "transcribe":
                check.setEnabled(False)
            check.toggled.connect(lambda checked, name=step.name: self._on_toggled(name, checked))
            self._checks[step.name] = check
            root.addWidget(check)

        root.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _default_name(recipe: Recipe) -> str:
        """Pre-filled name field: for a built-in, "<display name>
        (edited)" — nudging toward keeping the built-in itself untouched;
        for a recipe that's already a saved custom one, its own name."""
        if recipe.builtin_key:
            return tr("recipe_editor_default_name", base=tr(f"recipe_{recipe.builtin_key}"))
        return recipe.name

    def _on_toggled(self, name: str, checked: bool) -> None:
        if checked:
            for dep_name in _STEP_BY_NAME[name].depends_on:
                self._checks[dep_name].setChecked(True)
        else:
            for dependent in _DEPENDENTS.get(name, ()):
                self._checks[dependent].setChecked(False)

    def selected_steps(self) -> "tuple[str, ...]":
        """Steps in registry order, so a saved recipe's step order stays
        deterministic across edits."""
        return tuple(
            step.name for step in STEP_DEFINITIONS if self._checks[step.name].isChecked()
        )

    def recipe_name(self) -> str:
        """The name field's current value, falling back to the default it
        was pre-filled with if the user cleared it entirely."""
        return self._name_edit.text().strip() or self._default_recipe_name
