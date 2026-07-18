"""Privacy-safe, collapsible health view for a live session."""

from __future__ import annotations

import json

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QLabel, QPushButton

from config import get_config, save_config
from core.i18n import tr
from core.live.presentation import safe_metrics_snapshot
from ui.components import CollapsiblePanel


class LiveDiagnosticsPanel(CollapsiblePanel):
    def __init__(self, parent=None) -> None:
        super().__init__(tr("live_diagnostics"), parent=parent,
                         expanded=get_config().live_diagnostics_expanded)
        self.expanded_changed.connect(self._save_expanded)
        self.set_summary(tr("live_health_idle"))
        self.detail = QLabel(tr("live_health_idle"))
        self.detail.setWordWrap(True)
        self.body_layout.addWidget(self.detail)
        copy_btn = QPushButton(tr("live_copy_diagnostics"))
        copy_btn.clicked.connect(self.copy_diagnostics)
        self.body_layout.addWidget(copy_btn)
        self._safe: dict = {"state": "idle"}

    def set_metrics(self, metrics, session_state: str) -> None:
        self._safe = safe_metrics_snapshot(metrics, session_state)
        sources = self._safe["sources"]
        drops = sum(value["dropped_frames"] for value in sources.values())
        max_lag = max((value["lag_seconds"] for value in sources.values()), default=0.0)
        failed_source = any(value["state"] == "failed" for value in sources.values())
        if failed_source:
            health = tr("live_health_error")
        elif drops:
            health = tr("live_health_warning")
        elif max_lag > 3:
            health = tr("live_health_warning")
        else:
            health = tr("live_health_ok")
        self.set_summary(f"{health} · {tr('live_lag').format(seconds=max_lag)} · "
                         f"{tr('live_drops').format(count=drops) if drops else tr('live_no_drops')}")
        self.detail.setText(self.diagnostics_text())

    def diagnostics_text(self) -> str:
        return json.dumps(self._safe, ensure_ascii=False, indent=2, sort_keys=True)

    def copy_diagnostics(self) -> None:
        QGuiApplication.clipboard().setText(self.diagnostics_text())

    @staticmethod
    def _save_expanded(expanded: bool) -> None:
        get_config().live_diagnostics_expanded = expanded
        save_config()
