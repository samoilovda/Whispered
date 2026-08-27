"""Named bundles of export formats + generated materials (B9,
docs/IMPROVEMENT_PLAN_2026-08.ru.md) — "collect the YouTube package" as
one menu click instead of hand-picking formats, then separately hunting
down the article/insights/cover files a recipe already generated.

Qt-free (domain layer, per CLAUDE.md's architecture map) — a plain data
description of what a preset bundles; application/export_controller.py
does the actual file collection.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportPreset:
    """One export bundle.

    ``key`` names the preset (its display label lives at locale key
    ``export_preset_<key>``, kept in its own namespace rather than the
    unrelated, currently-unused ``preset_<key>`` entries left over from
    an earlier recipe-preset concept). ``formats`` are
    ``exporters.EXPORT_FORMATS`` keys; ``artifacts`` are
    ``application.steps.STEP_REGISTRY`` keys (the same strings
    ``domain.artifact.Artifact.type`` uses for these steps' output).
    """

    key: str
    formats: tuple[str, ...]
    artifacts: tuple[str, ...]


# Built-ins. A record missing one of a preset's artifacts (e.g. no
# YouTube package ever generated) does not disqualify the preset — see
# export_controller.export_preset()'s "skip with a report line, not an
# error" contract.
BUILTIN_EXPORT_PRESETS: tuple[ExportPreset, ...] = (
    ExportPreset(
        key="youtube",
        formats=("srt", "vtt"),
        artifacts=("youtube_package", "cover"),
    ),
    ExportPreset(
        key="article_draft",
        formats=("md", "docx"),
        artifacts=("article",),
    ),
    ExportPreset(
        key="archive",
        formats=("txt", "txt_ts", "srt", "vtt", "json", "md", "html", "docx", "pdf"),
        artifacts=("article", "insights", "youtube_package", "book", "cover"),
    ),
)

BUILTIN_EXPORT_PRESETS_BY_KEY: dict = {preset.key: preset for preset in BUILTIN_EXPORT_PRESETS}
