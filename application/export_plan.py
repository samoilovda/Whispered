"""What an export bundle will write, decided before anything is written
(UI/UX audit 2026-10-18, UX-15 / T15).

``build_export_plan()`` takes an immutable snapshot of the record — its
transcript revision, which formats and generated materials go where, how
fresh each material is, which targets already exist — and the UI shows it
for confirmation. ``execute_export_plan()`` writes exactly that snapshot;
if the transcript changed in between it refuses (``PlanOutdated``) rather
than export something the user didn't review. Exporting never touches a
material's own manifest.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from core.logger import get_logger
from domain.artifact_provenance import transcript_revision
from domain.transcription import TranscriptionResult

if TYPE_CHECKING:
    from domain.export_preset import ExportPreset

logger = get_logger(__name__)

CURRENT = "current"    # made from the transcript as it is now
STALE = "stale"        # made from an earlier revision
UNKNOWN = "unknown"    # no provenance to tell
MISSING = "missing"    # the record never produced it (or the file is gone)


class PlanOutdated(RuntimeError):
    """The record changed after the plan was shown."""


@dataclass(frozen=True)
class ExportItem:
    kind: str                     # "format" | "material"
    key: str                      # exporters format key / STEP_REGISTRY key
    target: str                   # file name inside the export folder
    source: str = ""              # material file to copy ("" for formats)
    freshness: str = CURRENT      # formats are always made from the current text
    exists: bool = False          # target already present in the folder
    same_file: bool = False       # target IS the source: never copied onto itself
    user_edits: tuple[str, ...] = ()  # edited keys kept in the overlay beside it
    overlay: str = ""             # the overlay file copied along, if any
    overlay_exists: bool = False  # the overlay's target is already present

    @property
    def will_write(self) -> bool:
        return self.freshness != MISSING and not self.same_file


@dataclass(frozen=True)
class ExportPlan:
    record_id: "int | str"
    revision: str
    directory: str
    default_name: str
    items: tuple[ExportItem, ...]
    index_exists: bool = False    # the folder already has an index file

    @property
    def conflicts(self) -> tuple[ExportItem, ...]:
        """Everything a replace would overwrite: targets, the edit overlay
        copied beside a material, and the folder's index file."""
        found = [i for i in self.items if (i.exists or i.overlay_exists) and i.will_write]
        if self.index_exists:
            found.append(ExportItem("index", "index", INDEX_NAME, exists=True))
        return tuple(found)


INDEX_NAME = "index.txt"


@dataclass
class ExportReport:
    written: list[str] = field(default_factory=list)       # target names
    failed: list[tuple[str, str]] = field(default_factory=list)  # (target, error)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (key, reason)
    index_path: str = ""


def _revision(result: TranscriptionResult) -> str:
    return transcript_revision(result.segments, result.language,
                               getattr(result, "speaker_names", None))


def build_export_plan(
    result: TranscriptionResult,
    preset: "ExportPreset",
    directory: str,
    record_id: "int | str",
    *,
    media_source: str = "",
    artifact_source: str = "",
    default_name: str = "transcript",
) -> ExportPlan:
    """The plan for exporting *preset* of this record into *directory*.

    *artifact_source* names the record's output folder (the record's
    original source path — not where its media is now, which may have been
    relinked); *media_source* is the media used for provenance."""
    from application.export_controller import format_extension
    from application.steps import STEP_REGISTRY, StepContext
    from application.user_edits import BASE_SUFFIX, load_overlay, overlay_path
    from core.paths import artifact_dir as artifact_dir_for
    from infrastructure.persistence import artifact_store

    folder = Path(directory)
    revision = _revision(result)
    items: list[ExportItem] = []
    for format_key in preset.formats:
        suffix = "_ts" if format_key == "txt_ts" else ""
        target = f"{default_name}{suffix}.{format_extension(format_key)}"
        items.append(ExportItem("format", format_key, target, exists=(folder / target).exists()))

    context = StepContext(
        source_path=media_source, result=result, record_id=record_id,
        artifact_dir=artifact_dir_for(record_id, artifact_source or media_source or "recording"),
    )
    for key in preset.artifacts:
        step = STEP_REGISTRY.get(key)
        artifact = step.make_artifact(context) if step is not None else None
        source = Path(artifact.path) if artifact is not None else None
        if source is None or not source.is_file():
            items.append(ExportItem("material", key, source.name if source else key,
                                    freshness=MISSING))
            continue
        saved = artifact_store.load(source)
        if saved is None or not saved.transcript_revision:
            freshness = UNKNOWN
        elif saved.transcript_revision == revision:
            freshness = CURRENT
        else:
            freshness = STALE
        overlay_file = overlay_path(source)
        edits = tuple(sorted(k for k in load_overlay(overlay_file) if not k.endswith(BASE_SUFFIX)))
        target_path = folder / source.name
        same = _same_path(target_path, source)
        items.append(ExportItem(
            "material", key, source.name, str(source), freshness,
            exists=target_path.exists(), same_file=same, user_edits=edits,
            overlay=str(overlay_file) if edits else "",
            overlay_exists=bool(edits) and (folder / overlay_file.name).exists(),
        ))
    return ExportPlan(record_id, revision, str(folder), default_name, tuple(items),
                      index_exists=(folder / INDEX_NAME).exists())


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def execute_export_plan(
    plan: ExportPlan, result: TranscriptionResult, *, overwrite: bool,
    index_name: str = INDEX_NAME,
) -> ExportReport:
    """Write *plan*. Existing targets, edit overlays and the index are kept
    unless *overwrite* (a kept index means the new one is written as
    ``index (2).txt``, ``index (3).txt``, …); one item failing never stops
    the others; the index lists exactly what was written, with each
    material's freshness and user edits."""
    from exporters import export_result

    if _revision(result) != plan.revision:
        raise PlanOutdated("the transcript changed after the export was reviewed")
    report = ExportReport()
    folder = Path(plan.directory)
    lines: list[str] = []
    for item in plan.items:
        target = folder / item.target
        if item.freshness == MISSING:
            report.skipped.append((item.key, "missing"))
            lines.append(f"{item.key}: not generated for this record")
            continue
        if item.same_file:
            report.skipped.append((item.key, "same_file"))
            lines.append(f"{item.key}: {item.target} (already in this folder — not copied)")
            continue
        if target.exists() and not overwrite:
            report.skipped.append((item.key, "exists"))
            lines.append(f"{item.key}: {item.target} (kept the existing file)")
            continue
        try:
            if item.kind == "format":
                export_result(result, str(target), item.key)
            else:
                shutil.copyfile(item.source, target)
                if item.overlay:
                    overlay_target = folder / Path(item.overlay).name
                    if overlay_target.exists() and not overwrite:
                        report.skipped.append((item.key, "overlay_exists"))
                        lines.append(f"{item.key}: kept the existing {overlay_target.name}")
                    else:
                        shutil.copyfile(item.overlay, overlay_target)
        except Exception as exc:  # noqa: BLE001 - reported per item
            logger.warning("Export of %s failed: %s", item.key, exc)
            report.failed.append((item.target, str(exc)))
            verb = "export" if item.kind == "format" else "copy"
            lines.append(f"{item.key}: {verb} failed ({exc})")
            continue
        report.written.append(item.target)
        note = "" if item.kind == "format" else f" [{item.freshness}]"
        if item.user_edits:
            note += f" + your edits: {', '.join(item.user_edits)} ({Path(item.overlay).name})"
        lines.append(f"{item.key}: {item.target}{note}")
    index = folder / index_name
    if index.exists() and not overwrite:
        stem, suffix = index.stem, index.suffix
        number = 2
        while (folder / f"{stem} ({number}){suffix}").exists():
            number += 1
        index = folder / f"{stem} ({number}){suffix}"
    try:
        index.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report.index_path = str(index)
    except OSError as exc:
        logger.warning("Failed to write the export index: %s", exc)
        report.failed.append((index.name, str(exc)))
    return report


def summarize(plan: ExportPlan) -> dict[str, int]:
    """Counts the preview shows: what will be written, stale/unknown/missing."""
    counts = {"write": 0, CURRENT: 0, STALE: 0, UNKNOWN: 0, MISSING: 0, "conflicts": 0}
    for item in plan.items:
        counts[item.freshness] += 1
        if item.will_write:
            counts["write"] += 1
    counts["conflicts"] = len(plan.conflicts)
    return counts


def find_item(plan: ExportPlan, key: str) -> Optional[ExportItem]:
    return next((i for i in plan.items if i.key == key), None)
