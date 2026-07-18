"""Live transcription page: source controls, preflight, meters and transcript."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from core.live.contracts import SegmentState
from utils import format_duration


class LiveView(QWidget):
    """Presentation-only Live page; device/model ownership stays in runtime."""

    preflight_requested = pyqtSignal()
    start_requested = pyqtSignal()
    pause_requested = pyqtSignal(bool)
    stop_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._updates: dict[str, Any] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(12)

        title = QLabel(tr("live_title"))
        title.setProperty("role", "title")
        root.addWidget(title)

        controls = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.addItem(tr("live_source_mic"), "mic")
        self.source_combo.addItem(tr("live_source_system"), "system")
        self.source_combo.addItem(tr("live_source_both"), "both")
        controls.addWidget(self.source_combo)
        self.target_combo = QComboBox()
        self.target_combo.addItem("Zoom", "us.zoom.xos")
        self.target_combo.addItem("Google Meet (Chrome)", "com.google.Chrome")
        self.target_combo.addItem("Microsoft Teams", "com.microsoft.teams2")
        controls.addWidget(self.target_combo)
        self.preflight_btn = QPushButton(tr("live_preflight"))
        self.preflight_btn.clicked.connect(self.preflight_requested.emit)
        controls.addWidget(self.preflight_btn)
        self.start_btn = QPushButton(tr("live_start"))
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.clicked.connect(self.start_requested.emit)
        controls.addWidget(self.start_btn)
        self.pause_btn = QPushButton(tr("live_pause"))
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.toggled.connect(self.pause_requested.emit)
        controls.addWidget(self.pause_btn)
        self.stop_btn = QPushButton(tr("live_stop"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        controls.addWidget(self.stop_btn)
        root.addLayout(controls)

        self.preflight_label = QLabel(tr("live_preflight_hint"))
        self.preflight_label.setWordWrap(True)
        self.preflight_label.setProperty("role", "muted")
        root.addWidget(self.preflight_label)

        status = QHBoxLayout()
        self.elapsed_label = QLabel("00:00")
        status.addWidget(self.elapsed_label)
        self.mic_state = QLabel("Microphone: idle")
        status.addWidget(self.mic_state)
        self.mic_meter = self._meter()
        status.addWidget(self.mic_meter)
        self.system_state = QLabel("Meeting audio: idle")
        status.addWidget(self.system_state)
        self.system_meter = self._meter()
        status.addWidget(self.system_meter)
        self.lag_label = QLabel("lag 0.0s · drops 0")
        status.addWidget(self.lag_label)
        status.addStretch()
        root.addLayout(status)

        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.transcript.setPlaceholderText(tr("live_transcript_placeholder"))
        root.addWidget(self.transcript, stretch=1)

        self._timer = QTimer(self)
        self._timer.setInterval(500)

    @staticmethod
    def _meter() -> QProgressBar:
        meter = QProgressBar()
        meter.setRange(0, 100)
        meter.setValue(0)
        meter.setTextVisible(False)
        meter.setFixedWidth(80)
        meter.setFixedHeight(7)
        return meter

    def selected_sources(self) -> tuple[bool, bool]:
        value = self.source_combo.currentData()
        return value in {"mic", "both"}, value in {"system", "both"}

    def target_bundle_id(self) -> str:
        return str(self.target_combo.currentData())

    def show_preflight(self, checks) -> None:
        icons = {"pass": "✓", "warning": "!", "fail": "✕"}
        self.preflight_label.setText(
            "  ·  ".join(f"{icons[check.status.value]} {check.message}" for check in checks)
        )

    def set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.preflight_btn.setEnabled(not running)
        self.source_combo.setEnabled(not running)
        self.target_combo.setEnabled(not running)
        self.pause_btn.setEnabled(running)
        self.stop_btn.setEnabled(running)
        if not running:
            self.pause_btn.setChecked(False)

    def set_source_state(self, source: str, state: str) -> None:
        label = self.mic_state if source == "mic" else self.system_state
        name = "Microphone" if source == "mic" else "Meeting audio"
        label.setText(f"{name}: {state}")

    def set_level(self, source: str, value: float) -> None:
        meter = self.mic_meter if source == "mic" else self.system_meter
        meter.setValue(min(100, max(0, int(value / 0.3 * 100))))

    def set_metrics(self, metrics) -> None:
        self.elapsed_label.setText(format_duration(metrics.elapsed_seconds))
        source_stats = metrics.source_stats
        lag = max((stats.lag_seconds for stats in source_stats.values()), default=0.0)
        drops = sum(stats.dropped_frames for stats in source_stats.values())
        asr_lag = metrics.scheduler.max_wait_seconds if metrics.scheduler else 0.0
        self.lag_label.setText(f"lag {max(lag, asr_lag):.1f}s · drops {drops}")

    def accept_update(self, update) -> None:
        self._updates[update.segment_id] = update
        lines = []
        for item in sorted(
            self._updates.values(), key=lambda value: (value.segment.start, value.segment_id)
        ):
            speaker = item.segment.speaker or item.segment_id.split(":", 1)[0]
            marker = "" if item.state is SegmentState.FINAL else " …"
            lines.append(f"[{item.segment.start:07.1f}] {speaker}: {item.segment.text.strip()}{marker}")
        self.transcript.setPlainText("\n".join(lines))

    def reset_session(self) -> None:
        self._updates.clear()
        self.transcript.clear()
        self.elapsed_label.setText("00:00")
        self.lag_label.setText("lag 0.0s · drops 0")
