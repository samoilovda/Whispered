"""Coordinates exporting a transcription result to one or more formats.

Extracted from ui/main_window.py::_export_result (see
docs/archive/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R6-cont). This module owns the
"what to export and whether it worked" decision — Qt-free and directly
testable; the caller (ui/main_window.py) still owns file dialogs, message
boxes, and toasts, since those are legitimately UI concerns.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Sequence

from core.logger import get_logger
from domain.transcription import TranscriptionResult
from exporters import export_result

if TYPE_CHECKING:
    from domain.export_preset import ExportPreset

logger = get_logger(__name__)


def format_extension(format_key: str) -> str:
    """File extension for a format key — txt and txt_ts share ``.txt``."""
    return 'txt' if format_key in ('txt', 'txt_ts') else format_key


@dataclass
class ExportOutcome:
    """Per-format result of a multi-format export run."""
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def any_succeeded(self) -> bool:
        return bool(self.succeeded)

    @property
    def any_failed(self) -> bool:
        return bool(self.failed)


def export_single(result: TranscriptionResult, filepath: str, format_key: str) -> None:
    """Export *result* to *filepath* in *format_key*.

    Raises on failure — the single-file path shows the caller the actual
    exception, unlike the multi-format path below.
    """
    export_result(result, filepath, format_key)


def export_many_to_directory(
    result: TranscriptionResult,
    directory: str,
    format_keys: Sequence[str],
    default_name: str,
) -> ExportOutcome:
    """Export *result* in every format in *format_keys* into *directory*,
    one file per format.

    Never raises — a failure in one format must not stop the others; the
    caller reports ``ExportOutcome.failed`` instead.
    """
    outcome = ExportOutcome()
    for format_key in format_keys:
        ext = format_extension(format_key)
        suffix = '_ts' if format_key == 'txt_ts' else ''
        filepath = os.path.join(directory, f"{default_name}{suffix}.{ext}")
        try:
            export_result(result, filepath, format_key)
            outcome.succeeded.append(format_key)
        except Exception as exc:
            logger.warning("Failed to export %s: %s", format_key, exc)
            outcome.failed.append(format_key)
    return outcome


@dataclass
class PresetExportOutcome:
    """Result of export_preset() (B9, docs/IMPROVEMENT_PLAN_2026-08.ru.md)
    — the format half (same shape as a plain multi-format export) plus
    which generated materials actually got copied versus which the
    record never had, so the caller can show "collected N files, M
    materials missing" instead of a flat success/fail."""
    formats: ExportOutcome = field(default_factory=ExportOutcome)
    materials_copied: list[str] = field(default_factory=list)
    materials_missing: list[str] = field(default_factory=list)
    index_path: str = ""

    @property
    def total_files(self) -> int:
        return len(self.formats.succeeded) + len(self.materials_copied)

    @property
    def any_missing(self) -> bool:
        return bool(self.formats.failed) or bool(self.materials_missing)


def export_preset(
    result: TranscriptionResult,
    preset: "ExportPreset",
    directory: str,
    record_id: "int | str",
    source_path: str = "",
    default_name: str = "transcript",
    *,
    artifact_source: str = "",
    overwrite: bool = True,
) -> PresetExportOutcome:
    """Collect everything *preset* bundles into *directory*: every format
    in ``preset.formats``, plus a copy of every generated material in
    ``preset.artifacts`` that actually exists on disk for this record.

    A material the record never generated (no YouTube package run, no
    cover rendered, ...) is skipped with a line in ``index.txt`` — not an
    error — so the same preset works on a record that only has a bare
    transcript. This is application/export_plan.py's build + execute in
    one call (the menu shows the plan first; see ExportPlanDialog).
    *artifact_source* names the record's output folder when it differs
    from *source_path* (media relinked after transcription, UX-09).
    """
    from application.export_plan import MISSING, build_export_plan, execute_export_plan

    plan = build_export_plan(
        result, preset, directory, record_id, media_source=source_path,
        artifact_source=artifact_source or source_path, default_name=default_name,
    )
    report = execute_export_plan(plan, result, overwrite=overwrite)
    return outcome_from_report(plan, report, MISSING)


def outcome_from_report(plan, report, missing: str = "missing") -> PresetExportOutcome:
    """A PresetExportOutcome view of an executed export plan."""
    outcome = PresetExportOutcome(index_path=report.index_path)
    written = set(report.written)
    failed = {target for target, _error in report.failed}
    for item in plan.items:
        if item.kind == "format":
            if item.target in written:
                outcome.formats.succeeded.append(item.key)
            elif item.target in failed:
                outcome.formats.failed.append(item.key)
        elif item.target in written:
            outcome.materials_copied.append(item.key)
        elif item.freshness == missing or item.target in failed:
            outcome.materials_missing.append(item.key)
    return outcome
