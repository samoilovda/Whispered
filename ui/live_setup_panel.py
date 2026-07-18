"""Session setup controls and non-blocking capture target discovery."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from config import get_config
from core.i18n import tr
from core.live.preflight import default_helper_path
from core.live.target_discovery import DiscoveredTarget, discover_targets
from ui.components import FormSection
from utils import WHISPER_LANGUAGES, WHISPER_MODELS


class TargetDiscoveryWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, helper_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self._helper_path = helper_path

    def run(self) -> None:
        try:
            self.completed.emit(discover_targets(
                self._helper_path, cancelled=self.isInterruptionRequested
            ))
        except Exception as exc:
            self.failed.emit(str(exc))


class LiveSetupPanel(FormSection):
    setup_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(tr("live_setup_title"), tr("live_setup_description"), parent)
        self._target_worker: TargetDiscoveryWorker | None = None
        self._build()

    def _build(self) -> None:
        sources = QHBoxLayout()
        self.mic_check = QCheckBox(tr("live_source_mic"))
        self.mic_check.setChecked(True)
        self.system_check = QCheckBox(tr("live_source_system"))
        self.system_check.setChecked(True)
        sources.addWidget(self.mic_check)
        sources.addWidget(self.system_check)
        sources.addStretch()
        self.layout.addLayout(sources)

        form = QFormLayout()
        self.mic_combo = QComboBox()
        self.mic_combo.addItem(tr("live_default_microphone"), get_config().mic_device_index)
        form.addRow(tr("live_mic_device"), self.mic_combo)

        target_row = QWidget()
        target_layout = QHBoxLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        self.target_combo = QComboBox()
        self.target_combo.addItem(tr("live_zoom_missing"), None)
        self.refresh_btn = QPushButton(tr("live_refresh_targets"))
        self.refresh_btn.clicked.connect(self.refresh_targets)
        target_layout.addWidget(self.target_combo, 1)
        target_layout.addWidget(self.refresh_btn)
        self._target_row = target_row
        form.addRow(tr("live_meeting_target"), target_row)

        self.model_combo = QComboBox()
        for key, label in WHISPER_MODELS:
            self.model_combo.addItem(label.split(" - ", 1)[0], key)
        self._select(self.model_combo, get_config().default_model)
        form.addRow(tr("live_model"), self.model_combo)

        self.language_combo = QComboBox()
        for key, _label in WHISPER_LANGUAGES:
            self.language_combo.addItem(tr("language_auto") if key == "auto" else key.upper(), key)
        self._select(self.language_combo, get_config().default_language)
        form.addRow(tr("live_language"), self.language_combo)
        self.layout.addLayout(form)

        for control in (
            self.mic_combo,
            self.target_combo,
            self.model_combo,
            self.language_combo,
            self.refresh_btn,
        ):
            control.setMinimumHeight(30)

        self.target_status = QLabel(tr("live_targets_not_checked"))
        self.target_status.setProperty("role", "muted")
        self.layout.addWidget(self.target_status)

        for control in (
            self.mic_check,
            self.system_check,
            self.mic_combo,
            self.target_combo,
            self.model_combo,
            self.language_combo,
        ):
            signal = getattr(control, "toggled", None) or control.currentIndexChanged
            signal.connect(self._changed)
        self.system_check.toggled.connect(self._sync_target_visibility)
        self._sync_target_visibility(True)

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _changed(self, *_args) -> None:
        self.setup_changed.emit()

    def _sync_target_visibility(self, enabled: bool) -> None:
        self._target_row.setEnabled(enabled)
        self.target_status.setVisible(enabled)

    def refresh_targets(self) -> None:
        if self._target_worker and self._target_worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.target_status.setText(tr("live_targets_searching"))
        worker = TargetDiscoveryWorker(default_helper_path(), self)
        worker.completed.connect(self._targets_ready)
        worker.failed.connect(self._targets_failed)
        worker.finished.connect(lambda: self.refresh_btn.setEnabled(True))
        self._target_worker = worker
        worker.start()

    def _targets_ready(self, targets: tuple[DiscoveredTarget, ...]) -> None:
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        for target in targets:
            suffix = tr("live_target_running")
            self.target_combo.addItem(f"{target.display_name} — {suffix}", target)
        if not targets:
            self.target_combo.addItem(tr("live_zoom_missing"), None)
        self.target_combo.blockSignals(False)
        self.target_status.setText(
            tr("live_targets_found").format(count=len(targets))
            if targets else tr("live_targets_empty")
        )
        self.setup_changed.emit()

    def _targets_failed(self, message: str) -> None:
        self.target_status.setText(tr("live_targets_error").format(error=message))

    def selected_sources(self) -> tuple[bool, bool]:
        return self.mic_check.isChecked(), self.system_check.isChecked()

    def selected_target(self) -> DiscoveredTarget | None:
        target = self.target_combo.currentData()
        return target if isinstance(target, DiscoveredTarget) else None

    def set_locked(self, locked: bool) -> None:
        for control in self.findChildren(QWidget):
            control.setEnabled(not locked)

    def shutdown(self) -> None:
        if self._target_worker and self._target_worker.isRunning():
            self._target_worker.requestInterruption()
            self._target_worker.wait(1500)
