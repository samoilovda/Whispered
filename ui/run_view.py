"""Step feed for a job run: one collapsed row per step, expandable to that
step's viewer panel (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B4).

This is the first Track B phase visible to the user — a new screen, not
yet the app's default landing point. What it deliberately does not do:

- Reparent any of the app's existing generator panels (``ArticleView``,
  ``YouTubePanel``, ``InsightsPanel``, ``BookPanel``, ``CoverView``,
  ``CleanedTextView``) into a row. Those widgets are still embedded
  elsewhere (the inspector rail's materials stack, its own ``_stack``
  page, ...); moving one into a row here would pull it out of its current
  home for good, breaking the still-live legacy navigation the plan says
  stays parallel until B6. A step whose ``viewers`` entry is ``None``
  simply never shows a chevron — real per-generator viewer wiring lands
  alongside each generator's own migration in B5, once its panel stops
  living anywhere else.
- Drive a real ``JobRunner``. ``retry_requested``/``cancel_requested`` are
  plain signals a caller can connect once "recipe -> JobRunner -> this
  screen" is the actual launch path (B6); until then this widget only
  reflects whatever ``JobRun`` it's handed.

``RunStepRow.apply_outcome(None)`` is what "waiting" looks like — a step
with no ``StepOutcome`` yet in ``JobRun.outcomes``.
"""

from __future__ import annotations

import time
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from application.job_engine import JobRun
from core.i18n import tr
from domain.job import StepOutcome, StepStatus
from ui.components import StatusBadge
from ui.theme import SPACE_2, SPACE_4


_STATUS_STATE = {
    StepStatus.SUCCEEDED: "success",
    StepStatus.SKIPPED: "success",
    StepStatus.FAILED: "error",
    StepStatus.CANCELLED: "neutral",
}


class RunStepRow(QWidget):
    """One step, collapsed to a single row by default.

    ``viewer``, if given, is shown/hidden (never destroyed) in a body
    area below the header once the row is expanded — see the module
    docstring for why most rows have no viewer yet.
    """

    retry_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    regenerate_clicked = pyqtSignal()

    def __init__(self, name: str, label: str, viewer: Optional[QWidget] = None, parent=None) -> None:
        super().__init__(parent)
        self.name = name
        self._viewer = viewer
        self._started_at: Optional[float] = None
        # SUCCEEDED/SKIPPED only — a completed step whose cache a user
        # wants to force past (B1's "forced regeneration"). Offered via
        # this row's context menu rather than a second header button: the
        # header is already Cancel/Retry/chevron, and regenerating a
        # cache-valid step is deliberately less discoverable than retrying
        # a failed one.
        self._can_regenerate = False
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACE_2)

        header = QHBoxLayout()
        header.setSpacing(SPACE_2)
        self._name_label = QLabel(label)
        self._name_label.setMinimumWidth(160)
        header.addWidget(self._name_label)

        self._status_badge = StatusBadge()
        header.addWidget(self._status_badge)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setMaximumWidth(140)
        self._progress.setVisible(False)
        header.addWidget(self._progress)

        header.addStretch(1)

        self._cancel_button = QPushButton(tr("btn_cancel"))
        self._cancel_button.setProperty("variant", "danger")
        self._cancel_button.setVisible(False)
        self._cancel_button.clicked.connect(self.cancel_clicked.emit)
        header.addWidget(self._cancel_button)

        self._retry_button = QPushButton(tr("run_retry"))
        self._retry_button.setProperty("variant", "ghost")
        self._retry_button.setVisible(False)
        self._retry_button.clicked.connect(self.retry_clicked.emit)
        header.addWidget(self._retry_button)

        self._chevron = QToolButton()
        self._chevron.setCheckable(True)
        self._chevron.setArrowType(Qt.ArrowType.RightArrow)
        self._chevron.setVisible(False)
        self._chevron.setAccessibleName(tr("run_toggle_details"))
        self._chevron.toggled.connect(self._on_toggled)
        header.addWidget(self._chevron)

        root.addLayout(header)

        self._body = QWidget()
        self._body.setVisible(False)
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(SPACE_4, 0, 0, SPACE_2)
        if self._viewer is not None:
            body_layout.addWidget(self._viewer)
        root.addWidget(self._body)

        self.apply_outcome(None)

    def _on_toggled(self, expanded: bool) -> None:
        self._chevron.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._body.setVisible(expanded and self._viewer is not None)

    def is_expanded(self) -> bool:
        return self._chevron.isChecked()

    # ── state transitions ───────────────────────────────────────────────

    def set_running(self) -> None:
        self._started_at = time.monotonic()
        self._status_badge.set_status(tr("run_status_running"), "info")
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)
        self._cancel_button.setVisible(True)
        self._retry_button.setVisible(False)
        self._chevron.setVisible(self._viewer is not None)

    def set_progress(self, percent: int, message: str) -> None:
        self._progress.setRange(0, 100)
        self._progress.setValue(max(0, min(100, percent)))
        self._status_badge.set_status(message or tr("run_status_running"), "info")

    def apply_outcome(self, outcome: Optional[StepOutcome]) -> None:
        """Render *outcome* — or, if ``None``, the "hasn't started yet"
        (waiting) state. This is what a fixture ``JobRun`` drives directly
        (see ``RunView.bind_run``), and what a live run falls back to
        after ``JobRunner.set_runners()`` reruns/resets a step."""
        self._progress.setVisible(False)
        self._cancel_button.setVisible(False)
        if outcome is None:
            self._started_at = None
            self._retry_button.setVisible(False)
            self._chevron.setVisible(False)
            self._chevron.setChecked(False)
            self._can_regenerate = False
            self._status_badge.set_status(tr("run_status_waiting"), "neutral")
            return

        state = _STATUS_STATE[outcome.status]
        can_retry = outcome.status in (StepStatus.FAILED, StepStatus.CANCELLED)
        has_result = outcome.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED, StepStatus.FAILED)
        self._can_regenerate = outcome.status in (StepStatus.SUCCEEDED, StepStatus.SKIPPED)
        self._retry_button.setVisible(can_retry)
        self._chevron.setVisible(has_result and self._viewer is not None)
        if not (has_result and self._viewer is not None):
            self._chevron.setChecked(False)

        if outcome.status is StepStatus.SUCCEEDED:
            text = self._elapsed_text()
        elif outcome.status is StepStatus.SKIPPED:
            text = tr("run_status_skipped")
        elif outcome.status is StepStatus.FAILED:
            text = tr("status_error", error=outcome.error)
        else:  # CANCELLED
            text = tr("run_status_cancelled")
        self._status_badge.set_status(text, state)
        self._started_at = None

    def _elapsed_text(self) -> str:
        if self._started_at is None:
            return tr("run_status_done")
        elapsed = max(0, int(time.monotonic() - self._started_at))
        return f"{elapsed // 60}:{elapsed % 60:02d}"

    def _show_context_menu(self, pos) -> None:
        if not self._can_regenerate:
            return
        menu = QMenu(self)
        action = menu.addAction(tr("run_regenerate"))
        action.triggered.connect(self.regenerate_clicked.emit)
        menu.exec(self.mapToGlobal(pos))


class RunView(QWidget):
    """Collapsed step feed for one job run — see module docstring.

    *step_order* fixes the row order (top to bottom); *labels* maps step
    name -> already-translated display text; *viewers*, if given, maps
    step name -> the widget to show once that row is expanded (only steps
    present in the mapping ever grow a chevron).
    """

    retry_requested = pyqtSignal(str)
    cancel_requested = pyqtSignal()
    open_record_requested = pyqtSignal()
    regenerate_requested = pyqtSignal(str)

    def __init__(
        self,
        step_order: "tuple[str, ...] | list[str]",
        labels: dict,
        viewers: "Optional[dict]" = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._order = tuple(step_order)
        self._run: Optional[JobRun] = None
        self._rows: "dict[str, RunStepRow]" = {}
        self._labels = dict(labels)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # A step feed with no heading and no exit was the whole screen:
        # nothing named the recipe that was running, and when it ended the
        # user was left on a list of finished rows with no route to the
        # record those steps had just produced.
        header = QHBoxLayout()
        header.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, 0)
        header.setSpacing(SPACE_2)
        self._heading = QLabel("")
        self._heading.setProperty("role", "page-title")
        self._heading.setVisible(False)
        header.addWidget(self._heading, stretch=1)
        self._open_button = QPushButton(tr("run_open_record"))
        self._open_button.setProperty("variant", "primary")
        self._open_button.setVisible(False)
        self._open_button.clicked.connect(self.open_record_requested.emit)
        header.addWidget(self._open_button)
        root.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        rows_layout = QVBoxLayout(container)
        rows_layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        rows_layout.setSpacing(SPACE_2)
        for name in self._order:
            viewer = (viewers or {}).get(name)
            label = labels.get(name, name)
            row = RunStepRow(name, label, viewer)
            row.retry_clicked.connect(lambda name=name: self._on_retry(name))
            row.cancel_clicked.connect(self.cancel_requested.emit)
            row.regenerate_clicked.connect(lambda name=name: self._on_regenerate(name))
            self._rows[name] = row
            rows_layout.addWidget(row)
        rows_layout.addStretch(1)

        scroll.setWidget(container)
        root.addWidget(scroll)
        self._scroll = scroll

    def set_recipe_name(self, name: str) -> None:
        """Name the recipe this run belongs to, in the screen's heading.

        Hidden while unnamed rather than left as an empty page title, so a
        caller that only binds a JobRun (a fixture, or the gallery) does
        not get a blank band above the first step.
        """
        self._heading.setText(tr("run_heading", name=name) if name else "")
        self._heading.setVisible(bool(name))

    def set_finished(self, finished: bool) -> None:
        """Show (or hide) the way out of this screen.

        Deliberately a button rather than an automatic jump to the record:
        a finished run is exactly when someone wants to read what each
        step did, and yanking the view away would take that with it.
        """
        self._open_button.setVisible(finished)

    def rows(self) -> "dict[str, RunStepRow]":
        return dict(self._rows)

    def retriable_steps(self) -> "list[tuple[str, str]]":
        """(name, label) pairs for steps currently eligible for retry
        (FAILED/CANCELLED in the bound JobRun) — what the command palette
        offers as "Restart: <step>" (B8, docs/UI_REDESIGN_PLAN_2026-09.ru.md).
        Reads outcome status directly rather than a row's retry-button
        visibility, so it works whether or not this widget is actually
        shown (a hidden/never-shown QWidget's isVisible() is always
        False, regardless of what setVisible() was called with)."""
        if self._run is None:
            return []
        retriable = (StepStatus.FAILED, StepStatus.CANCELLED)
        return [
            (name, self._labels.get(name, name))
            for name in self._order
            if (outcome := self._run.outcomes.get(name)) is not None
            and outcome.status in retriable
        ]

    def retry_step(self, name: str) -> None:
        """Public entry point equivalent to clicking that row's retry
        button — same reset+emit _on_retry does. Used by the command
        palette (B8); a row's own retry_clicked already calls _on_retry
        directly."""
        self._on_retry(name)

    def regenerate_step(self, name: str) -> None:
        """Public entry point equivalent to picking "Generate again" from
        that row's context menu — same reset+emit _on_regenerate does
        (see its docstring for why this is a distinct signal from
        retry_requested, not just retry_step() under another name)."""
        self._on_regenerate(name)

    def bind_run(self, run: JobRun) -> None:
        """Sync every row from *run*.outcomes — the entry point a fixture
        (or a resumed, persisted run — see application/run_store.py) uses
        to render a snapshot without any live JobRunner signals at all.

        Rows are built once for the whole step registry (MainWindow hands
        this widget every ``STEP_DEFINITIONS`` name), but a recipe runs
        only its own subset — so a row the bound run's spec doesn't
        contain is hidden rather than left sitting at "waiting" forever,
        which read as a finished run still having steps to go.
        """
        self._run = run
        planned = {step.name for step in run.spec.steps}
        for name, row in self._rows.items():
            row.setVisible(name in planned)
            row.apply_outcome(run.outcomes.get(name))

    # ── JobRunner signal targets ────────────────────────────────────────
    # Connect these directly to a JobRunner's step_started/step_progress/
    # step_finished/job_finished signals once a caller actually drives one
    # (B6) — each is a plain slot, safe to call from Qt's own dispatch.

    def on_step_started(self, name: str) -> None:
        row = self._rows.get(name)
        if row is not None:
            row.set_running()

    def on_step_progress(self, name: str, percent: int, message: str) -> None:
        row = self._rows.get(name)
        if row is not None:
            row.set_progress(percent, message)

    def on_step_finished(self, name: str, outcome: StepOutcome) -> None:
        row = self._rows.get(name)
        if row is not None:
            row.apply_outcome(outcome)

    def on_job_finished(self, run: JobRun) -> None:
        self.bind_run(run)

    def _on_retry(self, name: str) -> None:
        if self._run is not None:
            # reset_step() also cascades to any dependent step that was
            # only CANCELLED because *name* hadn't succeeded yet — re-sync
            # every row, not just this one, so those go back to "waiting".
            self._run.reset_step(name)
            self.bind_run(self._run)
        self.retry_requested.emit(name)

    def _on_regenerate(self, name: str) -> None:
        """Context-menu "Generate again" on a SUCCEEDED/SKIPPED row (B1's
        forced regeneration). Resets the same way _on_retry does, but
        emits a distinct signal — the caller must delete that step's
        on-disk manifest before relaunching, or a still-valid cache would
        just mark it SKIPPED again."""
        if self._run is not None:
            self._run.reset_step(name)
            self.bind_run(self._run)
        self.regenerate_requested.emit(name)
