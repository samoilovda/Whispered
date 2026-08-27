"""Session setup controls and non-blocking capture target discovery."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
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
from core.base_worker import BaseWorker
from core.i18n import tr
from core.live.preflight import default_helper_path
from core.live.target_discovery import DiscoveredTarget, discover_targets
from core.platform_support import live_system_audio_unavailable_message, supports_live_system_audio
from core.worker_registry import WorkerRegistry
from ui.components import ElidingComboBox, FlowLayout, FormSection
from ui.option_labels import (
    model_state_suffix,
    whisper_language_options,
    whisper_model_options_with_state,
)


class TargetDiscoveryWorker(BaseWorker):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, helper_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self._helper_path = helper_path

    def _execute(self) -> None:
        self.completed.emit(discover_targets(
            self._helper_path, cancelled=self.is_cancelled
        ))

    def _on_error(self, msg: str) -> None:
        self.failed.emit(msg)


class LiveSetupPanel(FormSection):
    setup_changed = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(tr("live_setup_title"), tr("live_setup_description"), parent)
        self._target_worker: TargetDiscoveryWorker | None = None
        self._registry = WorkerRegistry(parent=self)
        self._build()

    def _build(self) -> None:
        # A plain QHBoxLayout squeezes both checkboxes below their label's
        # own width once the column is narrow (same bug as the Library
        # filter chips — see docs/UI_REDESIGN_PLAN_2026-09.ru.md, A2/A4);
        # FlowLayout wraps to a second row at full size instead.
        sources_widget = QWidget()
        sources = FlowLayout(sources_widget, spacing=12)
        self.mic_check = QCheckBox(tr("live_source_mic"))
        self.mic_check.setChecked(True)
        self.system_check = QCheckBox(tr("live_source_system"))
        system_audio_supported = supports_live_system_audio()
        self.system_check.setChecked(system_audio_supported)
        self.system_check.setEnabled(system_audio_supported)
        if not system_audio_supported:
            self.system_check.setToolTip(live_system_audio_unavailable_message())
        sources.addWidget(self.mic_check)
        sources.addWidget(self.system_check)
        self.body_layout.addWidget(sources_widget)

        # QFormLayout's own row-label QLabels (from addRow(str, ...)) get
        # compressed below their own sizeHint in a narrow column just like
        # any other unwrapped QLabel — "Приложение встречи" clipped to
        # "Прилож" (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, A4). Build the
        # label explicitly so it can wrap instead of hard-clipping.
        def _row_label(text: str) -> QLabel:
            label = QLabel(text)
            label.setWordWrap(True)
            return label

        form = QFormLayout()
        # This panel is a permanently narrow (~250px) sidebar column, not a
        # resizable form — QFormLayout's default side-by-side label/field
        # columns squeeze a row-label QLabel like "Приложение встречи"
        # below its own sizeHint here even after word-wrap is enabled
        # (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, A4). WrapLongRows is
        # Qt's own answer to exactly this: a row that doesn't fit side by
        # side stacks its label above its field instead.
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.mic_combo = ElidingComboBox()
        self.mic_combo.addItem(tr("live_default_microphone"), get_config().mic_device_index)
        form.addRow(_row_label(tr("live_mic_device")), self.mic_combo)

        target_row = QWidget()
        target_layout = QHBoxLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        self.target_combo = ElidingComboBox()
        self.target_combo.addItem(tr("live_zoom_missing"), None)
        self.refresh_btn = QPushButton(tr("live_refresh_targets"))
        self.refresh_btn.clicked.connect(self.refresh_targets)
        target_layout.addWidget(self.target_combo, 1)
        target_layout.addWidget(self.refresh_btn)
        self._target_row = target_row
        form.addRow(_row_label(tr("live_meeting_target")), target_row)

        self.model_combo = ElidingComboBox()
        self._fill_model_combo()
        self._select(self.model_combo, get_config().default_model)
        form.addRow(_row_label(tr("live_model")), self.model_combo)

        self.language_combo = ElidingComboBox()
        for key, label in whisper_language_options():
            self.language_combo.addItem(label, key)
        self._select(self.language_combo, get_config().default_language)
        form.addRow(_row_label(tr("live_language")), self.language_combo)
        self.body_layout.addLayout(form)

        for control in (
            self.mic_combo,
            self.target_combo,
            self.model_combo,
            self.language_combo,
            self.refresh_btn,
        ):
            control.setMinimumHeight(30)

        self.target_status = QLabel(
            tr("live_targets_not_checked") if system_audio_supported
            else live_system_audio_unavailable_message()
        )
        self.target_status.setProperty("role", "muted")
        self.target_status.setWordWrap(True)
        self.body_layout.addWidget(self.target_status)

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
        self._sync_target_visibility(system_audio_supported)

    def _fill_model_combo(self) -> None:
        """Model items get the same "· downloaded"/"· will download"
        suffix ui/transcribe_options.py's model combo does (B10,
        docs/IMPROVEMENT_PLAN_2026-08.ru.md) — kept to the truncated
        "Name (~size)" label (no " - description") this combo already
        used, the suffix appended after it."""
        self.model_combo.clear()
        for key, label, downloaded in whisper_model_options_with_state():
            short_label = label.split(" - ", 1)[0]
            self.model_combo.addItem(f"{short_label} {model_state_suffix(downloaded)}", key)

    def refresh_model_state(self) -> None:
        """Re-check which models are downloaded and rebuild the model
        combo's item text — called after Settings is applied, never on
        every dropdown open (see TranscribeOptions.refresh_model_state's
        docstring for why)."""
        current = self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self._fill_model_combo()
        idx = self.model_combo.findData(current)
        self.model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.model_combo.blockSignals(False)

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
        if not supports_live_system_audio():
            self.target_status.setText(live_system_audio_unavailable_message())
            return
        if self._target_worker and self._target_worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.target_status.setText(tr("live_targets_searching"))
        worker = TargetDiscoveryWorker(default_helper_path(), self)
        worker.completed.connect(self._targets_ready)
        worker.failed.connect(self._targets_failed)
        worker.finished.connect(lambda: self.refresh_btn.setEnabled(True))
        self._target_worker = worker
        self._registry.register(worker, name="target_discovery")
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
            self._target_worker.cancel()
            if not self._target_worker.wait(1500):
                self._registry.retire(self._target_worker)
            self._target_worker = None
