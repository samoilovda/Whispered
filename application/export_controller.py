"""Coordinates exporting a transcription result to one or more formats.

Extracted from ui/main_window.py::_export_result (see
docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R6-cont). This module owns the
"what to export and whether it worked" decision — Qt-free and directly
testable; the caller (ui/main_window.py) still owns file dialogs, message
boxes, and toasts, since those are legitimately UI concerns.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Sequence

from core.logger import get_logger
from domain.transcription import TranscriptionResult
from exporters import export_result

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
