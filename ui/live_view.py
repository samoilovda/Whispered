"""Production Live page composed from setup, status and transcript widgets."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QFrame,
    QVBoxLayout,
    QWidget,
)

from core.i18n import tr
from core.live.preflight import LivePreflight
from ui.components import FlowLayout, InlineBanner, StatusBadge
from ui.live_diagnostics_panel import LiveDiagnosticsPanel
from ui.live_preflight_panel import LivePreflightPanel
from ui.live_setup_panel import LiveSetupPanel
from ui.live_transcript_view import LiveTranscriptView
from ui.theme import SPACE_2
from utils import format_duration
from core.platform_support import supports_live_system_audio


class LiveView(QWidget):
    preflight_requested = pyqtSignal()
    start_requested = pyqtSignal()
    pause_requested = pyqtSignal(bool)
    stop_requested = pyqtSignal()
    open_record_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._preflight_valid = False
        self._state = "idle"
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(12)
        # No PageHeader/page-title here: LiveView is one source page inside
        # StartView's own single column (docs/UI_REDESIGN_PLAN_2026-09.ru.md,
        # B7) — a second "page-title" nested under StartView's title read as
        # two stacked screens rather than one. Just the two live session
        # badges, which are genuinely dynamic status this column needs.
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(SPACE_2)
        self.state_badge = StatusBadge(tr("live_health_idle"))
        status_row.addWidget(self.state_badge)
        self.privacy_badge = StatusBadge(tr("live_audio_not_saved"))
        self.privacy_badge.set_status(tr("live_audio_not_saved"), "success")
        status_row.addWidget(self.privacy_badge)
        status_row.addStretch()
        root.addLayout(status_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # The session row below wraps via FlowLayout instead of overflowing
        # horizontally, so this scroll area only ever needs to scroll
        # vertically — a horizontal bar here previously covered the Start/
        # Pause/Stop row it could never actually reach past (see
        # docs/UI_REDESIGN_PLAN_2026-09.ru.md, A4).
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        content = QVBoxLayout(body)
        # Right margin so row content (Start/Pause/Stop, the last widget in
        # each QHBoxLayout) doesn't sit flush against the scrollbar track —
        # widgetResizable already excludes the scrollbar's own width from
        # body's width, but with zero margin here the last button's edge
        # still touches it directly, reading as clipped.
        content.setContentsMargins(0, 0, 10, 0)
        content.setSpacing(12)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        # Setup, preflight and diagnostics are inspector content for a Live
        # draft.  Keep them owned by LiveView so its public API and runtime
        # wiring stay unchanged, but let WorkspaceShell mount this panel on
        # the right instead of treating Live as a top-level page.
        self.options_panel = QWidget()
        options_layout = QVBoxLayout(self.options_panel)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(12)

        self.setup = LiveSetupPanel()
        self.setup.setup_changed.connect(self.invalidate_preflight)
        options_layout.addWidget(self.setup)

        actions_widget = QWidget()
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions_widget.setLayout(actions)
        self.preflight_btn = QPushButton(tr("live_preflight"))
        self.preflight_btn.clicked.connect(self.preflight_requested.emit)
        actions.addWidget(self.preflight_btn)
        actions.addStretch()
        self.start_btn = QPushButton(tr("live_start"))
        self.start_btn.setProperty("variant", "primary")
        self.start_btn.setEnabled(False)
        self.start_btn.setToolTip(tr("tooltip_live_start_disabled"))
        self.start_btn.clicked.connect(self.start_requested.emit)
        actions.addWidget(self.start_btn)
        options_layout.addWidget(actions_widget)

        self.preflight_panel = LivePreflightPanel()
        options_layout.addWidget(self.preflight_panel)
        self.banner = InlineBanner()
        content.addWidget(self.banner)

        # A fixed-width meter can't shrink and a QHBoxLayout won't wrap, so
        # a narrow center column previously forced this whole row wider
        # than the scroll area's viewport — the actual cause of the
        # horizontal scrollbar above, and it could push Pause/Stop/Open
        # out of view since nothing here ever wrapped or elided (see
        # docs/UI_REDESIGN_PLAN_2026-09.ru.md, A4). FlowLayout wraps
        # instead, so every control stays fully visible.
        session_widget = QWidget()
        session = FlowLayout(session_widget, spacing=10)
        self.elapsed_label = QLabel("00:00")
        session.addWidget(self.elapsed_label)
        self.mic_state = QLabel(tr("live_source_idle").format(source=tr("live_source_mic")))
        session.addWidget(self.mic_state)
        self.mic_meter = self._meter()
        session.addWidget(self.mic_meter)
        self.system_state = QLabel(tr("live_source_idle").format(source=tr("live_zoom")))
        self.system_state.setVisible(supports_live_system_audio())
        session.addWidget(self.system_state)
        self.system_meter = self._meter()
        self.system_meter.setVisible(supports_live_system_audio())
        session.addWidget(self.system_meter)
        self.pause_btn = QPushButton(tr("live_pause"))
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.toggled.connect(self._pause_toggled)
        session.addWidget(self.pause_btn)
        self.stop_btn = QPushButton(tr("live_stop"))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_requested.emit)
        session.addWidget(self.stop_btn)
        self.open_btn = QPushButton(tr("live_open_record"))
        self.open_btn.setProperty("variant", "primary")
        self.open_btn.setVisible(False)
        self.open_btn.clicked.connect(self.open_record_requested.emit)
        session.addWidget(self.open_btn)
        content.addWidget(session_widget)

        self.transcript = LiveTranscriptView()
        self.transcript.setMinimumHeight(180)
        content.addWidget(self.transcript, 1)
        self.diagnostics = LiveDiagnosticsPanel()
        options_layout.addWidget(self.diagnostics)
        options_layout.addStretch()
        self._timer = QTimer(self)
        self._timer.setInterval(500)

    @staticmethod
    def _meter() -> QProgressBar:
        meter = QProgressBar()
        meter.setRange(0, 100)
        meter.setValue(0)
        meter.setTextVisible(False)
        meter.setFixedWidth(90)
        meter.setFixedHeight(8)
        return meter

    def selected_sources(self) -> tuple[bool, bool]:
        return self.setup.selected_sources()

    def selected_model(self) -> str:
        return str(self.setup.model_combo.currentData())

    def selected_language(self) -> str:
        return str(self.setup.language_combo.currentData())

    def selected_mic_device(self) -> int | None:
        return self.setup.mic_combo.currentData()

    def selected_target(self):
        return self.setup.selected_target()

    def shutdown(self) -> None:
        """Part of the Shutdownable protocol (ui/shutdownable.py)."""
        self.setup.shutdown()

    def _set_start_enabled(self, enabled: bool) -> None:
        self.start_btn.setEnabled(enabled)
        self.start_btn.setToolTip("" if enabled else tr("tooltip_live_start_disabled"))

    def invalidate_preflight(self) -> None:
        self._preflight_valid = False
        self._set_start_enabled(False)
        if self._state in {"idle", "ready"}:
            self._state = "idle"
            self.state_badge.set_status(tr("live_health_idle"), "neutral")

    def set_preflighting(self) -> None:
        self._state = "preflighting"
        self.preflight_btn.setEnabled(False)
        self._set_start_enabled(False)
        self.state_badge.set_status(tr("live_preflighting"), "info")

    def show_preflight(self, checks) -> None:
        self.preflight_btn.setEnabled(True)
        self.preflight_panel.show_checks(checks)
        self._preflight_valid = LivePreflight.can_start(checks)
        self._state = "ready" if self._preflight_valid else "idle"
        self._set_start_enabled(self._preflight_valid)
        self.state_badge.set_status(
            tr("live_ready") if self._preflight_valid else tr("live_not_ready"),
            "success" if self._preflight_valid else "error",
        )

    def set_session_state(self, state: str) -> None:
        self._state = state
        active = state in {"starting", "running", "paused", "finalizing"}
        self.setup.set_locked(active)
        self.preflight_btn.setEnabled(not active)
        self._set_start_enabled(state in {"ready", "failed"} and self._preflight_valid)
        self.pause_btn.setEnabled(state in {"running", "paused"})
        self.stop_btn.setEnabled(state in {"starting", "running", "paused"})
        self.open_btn.setVisible(state == "completed")
        labels = {
            "idle": (tr("live_health_idle"), "neutral", ""),
            "starting": (tr("live_starting"), "info", tr("live_starting")),
            "running": (tr("live_running"), "success", ""),
            "paused": (tr("live_paused"), "warning", tr("live_paused")),
            "finalizing": (tr("live_finalizing"), "info", tr("live_finalizing")),
            "completed": (tr("live_completed"), "success", tr("live_completed")),
            "failed": (tr("live_health_error"), "error", tr("live_session_failed")),
        }
        label, kind, banner = labels.get(state, (state, "neutral", ""))
        self.state_badge.set_status(label, kind)
        self.banner.set_message(banner, severity=kind if kind != "neutral" else "info")
        if state != "paused" and self.pause_btn.isChecked():
            self.pause_btn.blockSignals(True)
            self.pause_btn.setChecked(False)
            self.pause_btn.blockSignals(False)

    def _pause_toggled(self, paused: bool) -> None:
        self.pause_btn.setText(tr("live_resume") if paused else tr("live_pause"))
        self.pause_requested.emit(paused)

    def set_source_state(self, source: str, state: str) -> None:
        label = self.mic_state if source == "mic" else self.system_state
        name = tr("live_source_mic") if source == "mic" else tr("live_zoom")
        localized_state = tr(f"live_state_{state}")
        label.setText(tr("live_source_state").format(source=name, state=localized_state))
        if state == "failed" and self._state in {"running", "paused"}:
            self.banner.set_message(
                tr("live_source_failed_continues").format(source=name), severity="warning"
            )

    def set_level(self, source: str, value: float) -> None:
        meter = self.mic_meter if source == "mic" else self.system_meter
        meter.setValue(min(100, max(0, int(value / 0.3 * 100))))

    def set_metrics(self, metrics) -> None:
        self.elapsed_label.setText(format_duration(metrics.elapsed_seconds))
        self.diagnostics.set_metrics(metrics, self._state)

    def accept_update(self, update) -> None:
        self.transcript.accept_update(update)

    def reset_session(self) -> None:
        self.transcript.reset()
        self.elapsed_label.setText("00:00")
        self.mic_meter.setValue(0)
        self.system_meter.setValue(0)
