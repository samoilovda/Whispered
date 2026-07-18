"""Asynchronous readiness checks and their visible blocking policy."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from core.i18n import tr
from core.live.preflight import LivePreflight, PreflightCheck, PreflightStatus
from ui.components import FormSection, StatusBadge


class LivePreflightWorker(QThread):
    completed = pyqtSignal(object)

    def __init__(self, *, use_mic: bool, use_system: bool, model_name: str,
                 target_available: bool, helper_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._options = dict(use_mic=use_mic, use_system=use_system,
                             model_name=model_name, target_available=target_available,
                             helper_path=helper_path)

    def run(self) -> None:
        self.completed.emit(LivePreflight().run(**self._options))


class LivePreflightPanel(FormSection):
    def __init__(self, parent=None) -> None:
        super().__init__(tr("live_ready_title"), tr("live_ready_required"), parent)
        self.setVisible(False)

    def show_checks(self, checks: tuple[PreflightCheck, ...]) -> None:
        while self.layout.count() > 2:
            item = self.layout.takeAt(2)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        for check in checks:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 2, 0, 2)
            kind = {
                PreflightStatus.PASS: "success",
                PreflightStatus.WARNING: "warning",
                PreflightStatus.FAIL: "error",
            }[check.status]
            label = {
                PreflightStatus.PASS: tr("preflight_pass"),
                PreflightStatus.WARNING: tr("preflight_warning"),
                PreflightStatus.FAIL: tr("preflight_fail"),
            }[check.status]
            layout.addWidget(StatusBadge(label, kind))
            message = QLabel(self._localized_message(check))
            message.setWordWrap(True)
            layout.addWidget(message, 1)
            self.layout.addWidget(row)
        self.setVisible(True)

    @staticmethod
    def _localized_message(check: PreflightCheck) -> str:
        suffix = "pass" if check.status is PreflightStatus.PASS else (
            "warning" if check.status is PreflightStatus.WARNING else "fail"
        )
        key = f"preflight_{check.key}_{suffix}"
        localized = tr(key)
        return check.message if localized == key else localized
