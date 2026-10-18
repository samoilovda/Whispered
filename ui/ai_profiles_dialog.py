"""Local AI profiles and prompt templates (N05): one modal dialog, rebuilt
each time it opens (like the other .exec() dialogs, so it is not
retranslated while open).

Profiles name an LM Studio endpoint/model/context/temperature; templates are
the user's editable copies of built-in prompts. Neither holds a key, import
validates against the schema in ``domain.ai_profile``, and an imported
prompt is only ever text sent to the model.
"""

from __future__ import annotations

import json

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from application import ai_profiles
from config import get_config, save_config
from core import prompt_templates
from core.i18n import tr
from core.logger import get_logger
from core.lm_status_worker import LMStatusWorker
from domain import ai_profile as model
from ui.theme import set_role

logger = get_logger(__name__)


class AiProfilesDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("ai_profiles_title"))
        self.setMinimumSize(640, 460)
        self._checker: "LMStatusWorker | None" = None
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._build_profiles_tab(), tr("ai_profiles_tab"))
        tabs.addTab(self._build_templates_tab(), tr("ai_templates_tab"))
        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
        self._reload_profiles()
        self._reload_templates()

    # ================================================================ profiles

    def _build_profiles_tab(self) -> QWidget:
        page = QWidget()
        root = QHBoxLayout(page)
        left = QVBoxLayout()
        self.profile_list = QListWidget()
        self.profile_list.setAccessibleName(tr("ai_profiles_tab"))
        self.profile_list.currentRowChanged.connect(self._show_profile)
        left.addWidget(self.profile_list)
        for key, slot in (("ai_profile_new", self._new_profile),
                          ("ai_profile_delete", self._delete_profile),
                          ("ai_profile_use", self._use_profile)):
            button = QPushButton(tr(key))
            button.clicked.connect(slot)
            left.addWidget(button)
        io_row = QHBoxLayout()
        for key, slot in (("ai_profile_import", self._import_profile),
                          ("ai_profile_export", self._export_profile)):
            button = QPushButton(tr(key))
            button.clicked.connect(slot)
            io_row.addWidget(button)
        left.addLayout(io_row)
        root.addLayout(left, 1)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow(tr("ai_profile_name"), self.name_edit)
        self.endpoint_edit = QLineEdit()
        self.endpoint_edit.setPlaceholderText("http://localhost:1234/v1")
        form.addRow(tr("ai_profile_endpoint"), self.endpoint_edit)
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText(tr("ai_profile_model_auto"))
        form.addRow(tr("ai_profile_model"), self.model_edit)
        self.context_spin = QSpinBox()
        self.context_spin.setRange(model.MIN_CONTEXT, model.MAX_CONTEXT)
        self.context_spin.setSingleStep(4000)
        form.addRow(tr("ai_profile_context"), self.context_spin)
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setDecimals(1)
        form.addRow(tr("ai_profile_temperature"), self.temperature_spin)
        steps_row = QHBoxLayout()
        self.step_checks: dict[str, QCheckBox] = {}
        for step in model.LLM_STEPS:
            check = QCheckBox(tr(f"step_{step}"))
            self.step_checks[step] = check
            steps_row.addWidget(check)
        steps_box = QWidget()
        steps_box.setLayout(steps_row)
        form.addRow(tr("ai_profile_steps"), steps_box)
        test_row = QHBoxLayout()
        self.test_btn = QPushButton(tr("btn_test_connection"))
        self.test_btn.clicked.connect(self._test_profile)
        test_row.addWidget(self.test_btn)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        test_row.addWidget(self.test_result, 1)
        form.addRow(test_row)
        self.save_btn = QPushButton(tr("ai_profile_save"))
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.clicked.connect(self._save_profile)
        form.addRow(self.save_btn)
        self.profile_error = QLabel("")
        self.profile_error.setWordWrap(True)
        set_role(self.profile_error, "danger-text")
        form.addRow(self.profile_error)
        self.profile_note = QLabel(tr("ai_profile_note"))
        self.profile_note.setWordWrap(True)
        self.profile_note.setProperty("role", "muted")
        form.addRow(self.profile_note)
        root.addLayout(form, 2)
        return page

    def _reload_profiles(self, select: str = "") -> None:
        cfg = get_config()
        self._profiles = ai_profiles.all_profiles(cfg)
        active = ai_profiles.active_profile(cfg).name
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for profile in self._profiles:
            suffix = "  ✓ " + tr("ai_profile_in_use") if profile.name == active else ""
            self.profile_list.addItem(profile.name + suffix)
        self.profile_list.blockSignals(False)
        row = next((i for i, p in enumerate(self._profiles) if p.name == (select or active)), 0)
        self.profile_list.setCurrentRow(row)
        self._show_profile(row)

    def _current_profile(self) -> "model.LmProfile | None":
        row = self.profile_list.currentRow()
        return self._profiles[row] if 0 <= row < len(self._profiles) else None

    def _show_profile(self, _row: int) -> None:
        profile = self._current_profile()
        if profile is None:
            return
        builtin = profile.name == model.DEFAULT_PROFILE
        self.name_edit.setText(profile.name)
        self.endpoint_edit.setText(profile.endpoint)
        self.model_edit.setText(profile.model)
        self.context_spin.setValue(profile.context_chars)
        self.temperature_spin.setValue(profile.temperature)
        for step, check in self.step_checks.items():
            check.setChecked(step in profile.steps)
        for widget in (self.name_edit, self.endpoint_edit, self.model_edit, self.context_spin,
                       self.temperature_spin, self.save_btn, *self.step_checks.values()):
            widget.setEnabled(not builtin)
        self.profile_error.setText("")
        self.test_result.setText("")
        note = tr("ai_profile_default_note") if builtin else (
            "" if profile.is_local else tr("ai_profile_remote_warning"))
        self.profile_note.setText(note or tr("ai_profile_note"))

    def _form_profile(self) -> "model.LmProfile":
        return model.profile_from_dict({
            "name": self.name_edit.text(), "endpoint": self.endpoint_edit.text(),
            "model": self.model_edit.text(), "context_chars": self.context_spin.value(),
            "temperature": self.temperature_spin.value(),
            "steps": [s for s, c in self.step_checks.items() if c.isChecked()],
        })

    def _save_profile(self) -> None:
        current = self._current_profile()
        try:
            profile = self._form_profile()
        except model.ProfileError as exc:
            self.profile_error.setText(str(exc))
            return
        if profile.name == model.DEFAULT_PROFILE:
            self.profile_error.setText(tr("ai_profile_name_reserved"))
            return
        ai_profiles.save_profile(get_config(), profile,
                                 replace=current.name if current else None)
        save_config()
        self._reload_profiles(select=profile.name)

    def _new_profile(self) -> None:
        cfg = get_config()
        base = model.default_profile(cfg)
        n, name = 1, "Profile"
        existing = {p.name for p in self._profiles}
        while name in existing:
            n += 1
            name = f"Profile {n}"
        ai_profiles.save_profile(cfg, model.LmProfile(name, base.endpoint, "", base.context_chars,
                                                       base.temperature))
        save_config()
        self._reload_profiles(select=name)

    def _delete_profile(self) -> None:
        current = self._current_profile()
        if current is None or current.name == model.DEFAULT_PROFILE:
            return
        ai_profiles.delete_profile(get_config(), current.name)
        save_config()
        self._reload_profiles()

    def _use_profile(self) -> None:
        current = self._current_profile()
        if current is None:
            return
        get_config().lm_profile_active = "" if current.name == model.DEFAULT_PROFILE else current.name
        save_config()
        self._reload_profiles(select=current.name)

    def _test_profile(self) -> None:
        """Probe the endpoint off the GUI thread — a test of the connection,
        nothing is generated."""
        url = self.endpoint_edit.text().strip()
        if not url:
            return
        self.test_result.setText(tr("book_lm_checking"))
        worker = LMStatusWorker(url, parent=self)
        worker.status_ready.connect(self._on_tested)
        self._checker = worker
        worker.start()

    def _on_tested(self, connected: bool, detail: str) -> None:
        if connected:
            self.test_result.setText(tr("ai_profile_test_ok", model=detail or tr("ai_profile_no_model")))
        else:
            self.test_result.setText(tr("ai_profile_test_failed", detail=detail))

    def _export_profile(self) -> None:
        profile = self._current_profile()
        if profile is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("ai_profile_export"), f"{profile.name}.json")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(profile.to_dict(), handle, ensure_ascii=False, indent=2)

    def _import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("ai_profile_import"), "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as handle:
                profile = model.profile_from_dict(json.load(handle))
        except (OSError, ValueError, model.ProfileError) as exc:
            QMessageBox.warning(self, tr("ai_profile_import"), tr("ai_import_invalid", error=str(exc)))
            return
        if profile.name == model.DEFAULT_PROFILE:
            profile = model.LmProfile(f"{profile.name} (imported)", profile.endpoint, profile.model,
                                      profile.context_chars, profile.temperature, profile.steps)
        ai_profiles.save_profile(get_config(), profile)
        save_config()
        self._reload_profiles(select=profile.name)

    # ================================================================ templates

    def _build_templates_tab(self) -> QWidget:
        page = QWidget()
        root = QHBoxLayout(page)
        left = QVBoxLayout()
        self.template_list = QListWidget()
        self.template_list.setAccessibleName(tr("ai_templates_tab"))
        self.template_list.currentRowChanged.connect(self._show_template)
        left.addWidget(self.template_list)
        self.base_combo = QComboBox()
        self.base_combo.setAccessibleName(tr("ai_template_base"))
        self.base_combo.addItems(prompt_templates.builtin_names())
        left.addWidget(self.base_combo)
        copy_btn = QPushButton(tr("ai_template_copy"))
        copy_btn.clicked.connect(self._copy_builtin)
        left.addWidget(copy_btn)
        delete_btn = QPushButton(tr("ai_template_delete"))
        delete_btn.clicked.connect(self._delete_template)
        left.addWidget(delete_btn)
        io_row = QHBoxLayout()
        for key, slot in (("ai_profile_import", self._import_template),
                          ("ai_profile_export", self._export_template)):
            button = QPushButton(tr(key))
            button.clicked.connect(slot)
            io_row.addWidget(button)
        left.addLayout(io_row)
        root.addLayout(left, 1)

        right = QVBoxLayout()
        self.template_info = QLabel("")
        self.template_info.setWordWrap(True)
        right.addWidget(self.template_info)
        self.template_text = QPlainTextEdit()
        self.template_text.setAccessibleName(tr("ai_template_text"))
        right.addWidget(self.template_text, 3)
        right.addWidget(QLabel(tr("ai_template_examples")))
        self.example_in = QPlainTextEdit()
        self.example_in.setPlaceholderText(tr("ai_template_example_in"))
        self.example_in.setAccessibleName(tr("ai_template_example_in"))
        self.example_in.setMaximumHeight(70)
        right.addWidget(self.example_in)
        self.example_out = QPlainTextEdit()
        self.example_out.setPlaceholderText(tr("ai_template_example_out"))
        self.example_out.setAccessibleName(tr("ai_template_example_out"))
        self.example_out.setMaximumHeight(70)
        right.addWidget(self.example_out)
        self.use_check = QCheckBox(tr("ai_template_use"))
        right.addWidget(self.use_check)
        self.template_error = QLabel("")
        self.template_error.setWordWrap(True)
        set_role(self.template_error, "danger-text")
        right.addWidget(self.template_error)
        save_btn = QPushButton(tr("ai_template_save"))
        save_btn.setProperty("variant", "primary")
        save_btn.clicked.connect(self._save_template)
        right.addWidget(save_btn)
        root.addLayout(right, 2)
        return page

    def _reload_templates(self, select: str = "") -> None:
        self._templates = prompt_templates.list_templates()
        active = get_config().prompt_template_active or {}
        self.template_list.blockSignals(True)
        self.template_list.clear()
        for template in self._templates:
            used = "  ✓" if active.get(template.base) == template.name else ""
            self.template_list.addItem(f"{template.name} ({template.base}, v{template.version}){used}")
        self.template_list.blockSignals(False)
        row = next((i for i, t in enumerate(self._templates) if t.name == select), 0)
        if self._templates:
            self.template_list.setCurrentRow(row)
        self._show_template(row if self._templates else -1)

    def _current_template(self) -> "model.PromptTemplate | None":
        row = self.template_list.currentRow()
        return self._templates[row] if 0 <= row < len(self._templates) else None

    def _show_template(self, _row: int) -> None:
        template = self._current_template()
        self.template_error.setText("")
        enabled = template is not None
        for widget in (self.template_text, self.example_in, self.example_out, self.use_check):
            widget.setEnabled(enabled)
        if template is None:
            self.template_info.setText(tr("ai_template_none"))
            self.template_text.setPlainText("")
            self.example_in.setPlainText("")
            self.example_out.setPlainText("")
            self.use_check.setChecked(False)
            return
        self.template_info.setText(tr("ai_template_info", base=template.base, version=template.version))
        self.template_text.setPlainText(template.text)
        first = template.examples[0] if template.examples else None
        self.example_in.setPlainText(first.input if first else "")
        self.example_out.setPlainText(first.output if first else "")
        self.use_check.setChecked(
            (get_config().prompt_template_active or {}).get(template.base) == template.name)

    def _copy_builtin(self) -> None:
        base = self.base_combo.currentText()
        names = {t.name for t in self._templates}
        n, name = 1, f"My {base}"
        while name in names:
            n += 1
            name = f"My {base} {n}"
        prompt_templates.save(prompt_templates.make_copy(base, name))
        self._reload_templates(select=name)

    def _save_template(self) -> None:
        template = self._current_template()
        if template is None:
            return
        examples = []
        if self.example_in.toPlainText().strip() and self.example_out.toPlainText().strip():
            examples.append(model.TemplateExample(self.example_in.toPlainText(),
                                                  self.example_out.toPlainText()))
        try:
            updated = model.template_from_dict({
                **template.to_dict(), "text": self.template_text.toPlainText(),
                "examples": [{"input": e.input, "output": e.output} for e in examples],
            })
        except model.ProfileError as exc:
            self.template_error.setText(str(exc))
            return
        saved = prompt_templates.save(updated)
        active = dict(get_config().prompt_template_active or {})
        if self.use_check.isChecked():
            active[saved.base] = saved.name
        elif active.get(saved.base) == saved.name:
            active.pop(saved.base)
        get_config().prompt_template_active = active
        save_config()
        self._reload_templates(select=saved.name)

    def _delete_template(self) -> None:
        template = self._current_template()
        if template is None:
            return
        prompt_templates.delete(template.name)
        active = dict(get_config().prompt_template_active or {})
        if active.get(template.base) == template.name:
            active.pop(template.base)
            get_config().prompt_template_active = active
            save_config()
        self._reload_templates()

    def _export_template(self) -> None:
        template = self._current_template()
        if template is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("ai_profile_export"), f"{template.name}.json")
        if path:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(template.to_dict(), handle, ensure_ascii=False, indent=2)

    def _import_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("ai_profile_import"), "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as handle:
                template = model.template_from_dict(
                    json.load(handle), known_bases=set(prompt_templates.builtin_names()))
        except (OSError, ValueError, model.ProfileError) as exc:
            QMessageBox.warning(self, tr("ai_profile_import"), tr("ai_import_invalid", error=str(exc)))
            return
        # Imported, never activated by itself.
        prompt_templates.save(template)
        self._reload_templates(select=template.name)

    def done(self, result: int) -> None:  # noqa: D102 - QDialog
        checker = self._checker
        if checker is not None and checker.isRunning():
            checker.cancel()
            checker.wait(1500)
        super().done(result)
