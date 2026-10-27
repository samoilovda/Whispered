"""Background export / import of a portable project (N06): zipping or
unpacking a record with its media can take a while, so it never runs on
the GUI thread, and it can be cancelled."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import pyqtSignal

from application import project_archive as pa
from core.base_worker import BaseWorker
from domain.project_manifest import ProjectError


class ProjectWorker(BaseWorker):
    """``done(result)`` — the written ``ProjectManifest`` (export) or the
    new record id (import) — or ``failed(message)``; a cancel ends with
    ``cancelled()`` and nothing left behind. The dry runs that precede
    them (``plan_record`` → an export plan, ``inspect_path`` → an import
    plan) report with ``planned(plan)`` instead: they read every file's
    size or the archive's directory, which is not for the GUI thread."""

    progress = pyqtSignal(int, int)
    planned = pyqtSignal(object)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, *, export_plan: Optional[pa.ExportPlan] = None, path: str = "",
                 plan_record: Optional[int] = None, include_media: bool = False,
                 inspect_path: str = "", parent=None) -> None:
        super().__init__(parent)
        self._plan = export_plan
        self._path = path
        self._plan_record = plan_record
        self._include_media = include_media
        self._inspect_path = inspect_path

    def _on_error(self, msg: str) -> None:
        self._emit_terminal(self.failed, msg)

    def _execute(self) -> None:
        try:
            result: object
            if self._plan_record is not None:
                plan = pa.plan_export(self._plan_record, include_media=self._include_media)
                self._emit_terminal(self.planned, plan)
                return
            if self._inspect_path:
                preview = pa.inspect_archive(self._inspect_path)
                self._emit_terminal(self.planned, preview)
                return
            if self._plan is not None:
                result = pa.write_export(self._plan, self._path, cancelled=self._cancelled.is_set,
                                         progress=self.progress.emit)
            else:
                result = pa.import_archive(self._path, cancelled=self._cancelled.is_set,
                                           progress=self.progress.emit)
        except pa.ProjectCancelled:
            self._emit_terminal(self.cancelled)
            return
        except ProjectError as exc:
            self._emit_terminal(self.failed, str(exc))
            return
        self._emit_terminal(self.done, result)
