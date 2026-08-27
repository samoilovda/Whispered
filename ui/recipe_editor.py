""""Настроить…"/"Изменить" — the escape hatch from StartView's built-in
recipe chips (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B6).

Edits step selection (with dependency edges kept consistent — checking
"article" pulls in "clean", unchecking "clean" drops anything that needed
it), a name, and the transcription options widget (model/language/
translate/mode/diarization) it borrows from MainWindow. Since B4
(docs/IMPROVEMENT_PLAN_2026-08.ru.md) this is a real named-recipe editor,
not a single unnamed "custom" slot: Save/Save as new/Delete let a user
keep several named recipes side by side, each carrying its own
transcription params in Recipe.params rather than only a shared global
default.

The name field (docs/IMPROVEMENT_PLAN_2026-08.ru.md, A4) still matters
for the same reason it always did: without it, saving from this dialog
with no actual change made silently discarded whichever recipe the user
started from. The caller (MainWindow._open_recipe_editor) decides what
"Save"/"Save as new"/"Delete" actually do to Config.recipes — this
dialog only reports selected_steps()/recipe_name()/recipe_params() and
which button closed it (result_action), the same "dialog reports, caller
persists" split every other MainWindow dialog uses.

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
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
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
    def __init__(
        self,
        transcribe_options: QWidget,
        recipe: Recipe,
        existing_names: "frozenset[str] | set[str]" = frozenset(),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("recipe_editor_title"))
        self.resize(420, 660)
        self._transcribe_options = transcribe_options
        # Delete only makes sense for a recipe that's actually a saved
        # entry in Config.recipes — a built-in, or an edit not yet saved
        # under this name, has nothing there to remove.
        self._is_saved_custom = recipe.name in existing_names
        self.result_action: "str | None" = None  # "save" | "save_as_new" | "delete"

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
        # Seed the shared widget from *this recipe's* saved params before
        # showing it — any key the recipe doesn't override falls back to
        # whatever Config already has (see TranscribeOptions.apply_params).
        transcribe_options.apply_params(recipe.params)
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

        buttons_row = QHBoxLayout()
        self._delete_btn = QPushButton(tr("recipe_editor_delete"))
        self._delete_btn.setProperty("variant", "danger")
        self._delete_btn.setVisible(self._is_saved_custom)
        self._delete_btn.clicked.connect(self._on_delete)
        buttons_row.addWidget(self._delete_btn)
        buttons_row.addStretch(1)
        cancel_btn = QPushButton(tr("btn_cancel"))
        cancel_btn.clicked.connect(self.reject)
        buttons_row.addWidget(cancel_btn)
        save_as_new_btn = QPushButton(tr("recipe_editor_save_as_new"))
        save_as_new_btn.clicked.connect(self._on_save_as_new)
        buttons_row.addWidget(save_as_new_btn)
        save_btn = QPushButton(tr("recipe_editor_save"))
        save_btn.setProperty("variant", "primary")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        buttons_row.addWidget(save_btn)
        root.addLayout(buttons_row)

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

    def _on_save(self) -> None:
        self.result_action = "save"
        self.accept()

    def _on_save_as_new(self) -> None:
        self.result_action = "save_as_new"
        self.accept()

    def _on_delete(self) -> None:
        reply = QMessageBox.question(
            self,
            tr("recipe_editor_delete_title"),
            tr("recipe_editor_delete_confirm", name=self.recipe_name()),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.result_action = "delete"
        self.accept()

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

    def recipe_params(self) -> dict:
        """The transcription options widget's current selections (B4) —
        what the caller saves into the edited recipe's Recipe.params."""
        return self._transcribe_options.params()
