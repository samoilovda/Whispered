"""Exporting from the Record view: the Export menu (one format to a file,
several to a folder) and the presets that bundle formats with generated
materials. The decisions live in application/export_controller.py and
application/export_plan.py; this controller owns the dialogs, the worker
threads for the heavy parts and the toasts around them."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox

from application import export_controller
from core.i18n import tr
from exporters import EXPORT_FORMATS
from ui.toast import show_toast

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class ExportFlowController:
    def __init__(self, host: "MainWindow") -> None:
        self._host = host

    def export_result(self) -> None:
        """Export the transcription result using the formats currently
        checked in the Record view's Export menu.

        The actual export work (what succeeded, what failed) is delegated
        to application/export_controller.py; this method only owns the
        file dialog / message box / toast presentation around it.
        """
        host = self._host
        result = host.transcript_view.get_result()
        if not result:
            return

        format_keys = host.record_view.get_export_formats()
        source_file = host.file_selector.get_file() or "transcript"
        default_name = os.path.splitext(os.path.basename(source_file))[0]

        if len(format_keys) == 1:
            # Single format
            format_key = format_keys[0]
            format_name, _ = EXPORT_FORMATS[format_key]
            ext = export_controller.format_extension(format_key)
            filepath, _ = QFileDialog.getSaveFileName(
                host, tr("export_as_title", format=format_name), f"{default_name}.{ext}",
                f"{format_name} (*.{ext});;All Files (*)"
            )

            if filepath:
                try:
                    export_controller.export_single(result, filepath, format_key)
                    show_toast(host, tr("toast_exported_one", name=os.path.basename(filepath)), kind="success")
                except Exception as e:
                    QMessageBox.critical(host, tr("error_export"), str(e))
        else:
            # Multiple formats - directory
            directory = QFileDialog.getExistingDirectory(host, tr("export_dir_title"))
            if directory:
                overwrite = False
                clashes = export_controller.existing_paths(directory, format_keys, default_name)
                if clashes:
                    box = QMessageBox(host)
                    box.setIcon(QMessageBox.Icon.Question)
                    box.setWindowTitle(tr("error_export"))
                    box.setText(tr("export_exists_msg"))
                    box.setInformativeText("\n".join(os.path.basename(p) for p in clashes))
                    replace_btn = box.addButton(tr("export_replace"), QMessageBox.ButtonRole.DestructiveRole)
                    skip_btn = box.addButton(tr("export_skip_existing"), QMessageBox.ButtonRole.AcceptRole)
                    box.addButton(QMessageBox.StandardButton.Cancel)
                    box.exec()
                    if box.clickedButton() not in (replace_btn, skip_btn):
                        return
                    overwrite = box.clickedButton() is replace_btn
                outcome = export_controller.export_many_to_directory(
                    result, directory, format_keys, default_name, overwrite=overwrite
                )
                if outcome.any_succeeded:
                    show_toast(
                        host,
                        tr("toast_exported_many", count=len(outcome.succeeded)),
                        kind="success" if not outcome.any_failed else "warning",
                    )
                if outcome.any_failed:
                    QMessageBox.warning(
                        host,
                        tr("error_export"),
                        "Failed formats: " + ", ".join(outcome.failed),
                    )

    def export_preset(self, preset_key: str) -> None:
        """B9, docs/IMPROVEMENT_PLAN_2026-08.ru.md: collect an export
        preset's formats and generated materials into one folder — the
        actual collection is delegated to
        application/export_controller.py::export_preset(); this method
        only owns the directory dialog and the resulting toast."""
        host = self._host
        result = host.transcript_view.get_result()
        if not result:
            return
        from domain.export_preset import BUILTIN_EXPORT_PRESETS_BY_KEY
        preset = BUILTIN_EXPORT_PRESETS_BY_KEY.get(preset_key)
        if preset is None:
            return

        directory = QFileDialog.getExistingDirectory(host, tr("export_dir_title"))
        if not directory:
            return

        source_file = host.file_selector.get_file() or "transcript"
        default_name = os.path.splitext(os.path.basename(source_file))[0]
        record_id = host._last_record_id if host._last_record_id is not None else "unsaved"

        # UX-15: the bundle is planned, shown, and only then written —
        # exactly the reviewed snapshot (a changed transcript is refused).
        # Planning stats every material and writing copies them: both run
        # off the GUI thread.
        from application import export_plan

        media_source = host._source_filepath or ""
        artifact_source = host._artifact_source()
        self.run_call(
            lambda _cancelled: export_plan.build_export_plan(
                result, preset, directory, record_id, media_source=media_source,
                artifact_source=artifact_source, default_name=default_name),
            lambda plan: self._write_bundle(plan, result, directory),
            tr("export_plan_building"),
        )

    def _write_bundle(self, plan, result, directory: str) -> None:
        host = self._host
        from application import export_plan

        accepted, overwrite = self.review_plan(plan)
        if not accepted:
            return
        self.run_call(
            lambda _cancelled: export_plan.execute_export_plan(plan, result, overwrite=overwrite),
            lambda report: self._bundle_written(plan, report, directory),
            tr("export_plan_writing"),
            on_error=lambda exc: QMessageBox.information(
                host, tr("export_plan_title"), tr("export_plan_outdated"))
            if "changed after the export was reviewed" in exc else None,
        )

    def _bundle_written(self, plan, report, directory: str) -> None:
        host = self._host
        outcome = export_controller.outcome_from_report(plan, report)
        if outcome.any_missing:
            show_toast(
                host,
                tr(
                    "toast_export_preset_partial",
                    count=outcome.total_files, missing=len(outcome.materials_missing),
                ),
                kind="warning",
            )
        else:
            show_toast(
                host,
                tr("toast_export_preset_done", count=outcome.total_files, dir=directory),
                kind="success",
            )

    def run_call(self, fn, on_done, label: str, on_error=None) -> None:
        """Run *fn* off the GUI thread behind a status-bar operation, then
        continue with ``on_done(result)`` on the GUI thread. A failure is
        shown (or handed to *on_error*, which returns non-None when it
        handled the message)."""
        host = self._host
        from ui.call_worker import CallWorker

        host.status_bar.set_operation(label, cancellable=False)
        worker = CallWorker(fn, parent=host)

        def finish() -> None:
            if host.status_bar.status_label.text() == label:
                host.status_bar.clear()

        def done(result: object) -> None:
            finish()
            on_done(result)

        def failed(message: str) -> None:
            finish()
            if on_error is not None and on_error(message) is not None:
                return
            QMessageBox.warning(host, tr("error_export"), message)

        worker.done.connect(done)
        worker.failed.connect(failed)
        host._registry.register(worker, name=f"call_{id(worker)}")
        worker.start()

    def review_plan(self, plan) -> "tuple[bool, bool]":
        """Show the plan; (accepted, overwrite existing files)."""
        host = self._host
        from ui.export_plan_dialog import ExportPlanDialog

        dialog = ExportPlanDialog(plan, host)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return accepted, dialog.overwrite.isChecked()
