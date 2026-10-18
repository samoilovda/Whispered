"""The run screen's outcome summary (UI/UX audit 2026-10-18, UX-11 / T11)."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QPushButton

from application.job_engine import JobRun
from core.i18n import load_locale, tr
from domain.job import JobSpec, StepOutcome, StepSpec, StepStatus
from ui.run_view import RunView


def _view():
    order = ["transcribe", "clean", "article", "insights"]
    view = RunView(step_order=order, labels={n: n.title() for n in order})
    spec = JobSpec(name="r", steps=(
        StepSpec("transcribe"), StepSpec("clean", depends_on=("transcribe",)),
        StepSpec("article", depends_on=("clean",)), StepSpec("insights", depends_on=("transcribe",)),
    ))
    run = JobRun(spec=spec)
    run.outcomes.update({
        "transcribe": StepOutcome("transcribe", StepStatus.SUCCEEDED),
        "clean": StepOutcome("clean", StepStatus.FAILED, error="LM Studio is not responding (key sk-abcdefghijkl)"),
        "article": StepOutcome("article", StepStatus.CANCELLED),
        "insights": StepOutcome("insights", StepStatus.SUCCEEDED),
    })
    view.bind_run(run)
    return view


def _report_labels(view):
    return {label.property("run_report"): label for label in view._report_box.findChildren(QLabel)
            if label.property("run_report") and not label.isHidden()}


def test_partial_run_summary_names_reason_and_offers_recovery(process_events):
    load_locale("en")
    view = _view()
    view.show()
    assert view._report_box.isHidden()  # not before the run ends
    view.set_finished(True)
    process_events()
    labels = _report_labels(view)
    assert labels["headline"].text() == tr("run_report_partial")
    assert tr("run_report_blocked", count=1) in labels["counts"].text()
    assert tr("run_failure_provider_unavailable") in labels["failure:clean"].text()
    assert tr("library_chip_insights") in labels["materials"].text()

    fixes, retries = [], []
    view.fix_requested.connect(fixes.append)
    view.retry_requested.connect(retries.append)
    buttons = {b.text(): b for b in view._report_box.findChildren(QPushButton) if not b.isHidden()}
    buttons[tr("ready_fix_settings_ai")].click()
    buttons[tr("run_retry")].click()
    assert fixes == ["settings_ai"] and retries == ["clean"]


def test_summary_follows_the_language(process_events):
    load_locale("en")
    view = _view()
    view.set_finished(True)
    load_locale("ru")
    view._retranslate()
    try:
        assert _report_labels(view)["headline"].text() == tr("run_report_partial")
        assert "Подробности" in [b.text() for b in view._report_box.findChildren(QPushButton)
                                 if not b.isHidden()]
    finally:
        load_locale("en")
