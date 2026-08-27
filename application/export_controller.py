"""Coordinates exporting a transcription result to one or more formats.

Extracted from ui/main_window.py::_export_result (see
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R6-cont). This module owns the
"what to export and whether it worked" decision — Qt-free and directly
testable; the caller (ui/main_window.py) still owns file dialogs, message
boxes, and toasts, since those are legitimately UI concerns.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
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
) -> PresetExportOutcome:
    """Collect everything *preset* bundles into *directory*: every format
    in ``preset.formats`` (reusing export_many_to_directory), plus a copy
    of every generated material in ``preset.artifacts`` that actually
    exists on disk for this record.

    A material the record never generated (no YouTube package run, no
    cover rendered, ...) is skipped with a line in ``index.txt`` — not an
    error — so the same preset works on a record that only has a bare
    transcript. Materials are located the same deterministic way
    application/steps.py's own cache-skip does: STEP_REGISTRY[artifact_type]
    .make_artifact() with a StepContext built from *result*/*source_path*/
    *record_id*, not by re-running anything.
    """
    from application.steps import STEP_REGISTRY, StepContext
    from core.paths import artifact_dir as _artifact_dir_for
    from infrastructure.persistence import artifact_store

    outcome = PresetExportOutcome()
    outcome.formats = export_many_to_directory(result, directory, preset.formats, default_name)

    index_lines = []
    for format_key in outcome.formats.succeeded:
        ext = format_extension(format_key)
        suffix = '_ts' if format_key == 'txt_ts' else ''
        index_lines.append(f"{format_key}: {default_name}{suffix}.{ext}")
    for format_key in outcome.formats.failed:
        index_lines.append(f"{format_key}: export failed")

    context = StepContext(
        source_path=source_path,
        result=result,
        record_id=record_id,
        artifact_dir=_artifact_dir_for(record_id, source_path),
    )
    for artifact_type in preset.artifacts:
        step = STEP_REGISTRY.get(artifact_type)
        artifact = step.make_artifact(context) if step is not None else None
        source_file = Path(artifact.path) if artifact is not None else None
        if source_file is None or not source_file.is_file():
            outcome.materials_missing.append(artifact_type)
            index_lines.append(f"{artifact_type}: not generated for this record")
            continue
        dest = Path(directory) / source_file.name
        try:
            shutil.copyfile(source_file, dest)
        except OSError as exc:
            logger.warning("Failed to copy %s material for preset export: %s", artifact_type, exc)
            outcome.materials_missing.append(artifact_type)
            index_lines.append(f"{artifact_type}: copy failed ({exc})")
            continue
        outcome.materials_copied.append(artifact_type)
        manifest = artifact_store.load(source_file)
        when = manifest.created_at if manifest is not None else "?"
        index_lines.append(f"{artifact_type}: {dest.name} (generated {when})")

    index_path = Path(directory) / "index.txt"
    try:
        index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
        outcome.index_path = str(index_path)
    except OSError as exc:
        logger.warning("Failed to write preset export index.txt: %s", exc)

    return outcome
