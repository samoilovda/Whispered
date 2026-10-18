#!/usr/bin/env python3
"""Render deterministic UI states without touching user application data."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("WHISPERED_UI_GALLERY", "1")

from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QPushButton,
    QToolButton,
    QWidget,
)

import config
import core.history as history
from core.i18n import load_locale, tr
from ui.theme import apply_theme


SIZES = ((900, 550), (1100, 700), (1440, 900))
SECTIONS = ("library", "recorder", "live", "queue")

# A widget that elides its own text (QFontMetrics.elidedText + a tooltip
# carrying the full text) is not a defect — see ui/theme.py's ``mark_elides``
# helper. A QLabel with setWordWrap(True) isn't either: the check below
# compares actual width against sizeHint()'s *unwrapped* single-line
# width, which a wrapped label is expected to be narrower than — Qt
# reflows it across more lines instead of cutting anything off, so 100%
# of the text stays visible either way. Narrower still than either of
# those and the text really is cut off.
_TEXT_WIDGET_CLASSES = (QLabel, QPushButton, QToolButton, QCheckBox, QComboBox)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def _inside(parent, child) -> bool:
    top_left = child.mapTo(parent, child.rect().topLeft())
    bottom_right = child.mapTo(parent, child.rect().bottomRight())
    return parent.rect().contains(top_left) and parent.rect().contains(bottom_right)


def _widget_text(widget) -> str:
    if isinstance(widget, QComboBox):
        return widget.currentText()
    return widget.text() if hasattr(widget, "text") else ""


def clipped_text_widgets(window) -> list[tuple[str, int, int]]:
    """Visible text widgets narrower than their own ``sizeHint()``.

    A widget that elides its text on purpose (see ``mark_elides`` in
    ui/theme.py) is excluded — it has already chosen to be narrower than
    its full text and shows the full text via tooltip instead. So is a
    QLabel with setWordWrap(True): its full text also stays visible,
    reflowed across more lines instead of cut off (see the module-level
    comment on _TEXT_WIDGET_CLASSES).
    """
    bad: list[tuple[str, int, int]] = []
    for cls in _TEXT_WIDGET_CLASSES:
        for widget in window.findChildren(cls):
            if not widget.isVisible():
                continue
            if bool(widget.property("_elides")):
                continue
            if isinstance(widget, QLabel) and widget.wordWrap():
                continue
            text = _widget_text(widget)
            if len(text.strip()) < 3:
                continue
            needed = widget.sizeHint().width()
            if widget.width() < needed - 2:
                bad.append((text[:40], widget.width(), needed))
    return bad


def _needed_height(widget) -> int:
    """Height *widget* needs at its current width: a word-wrapped label
    reflows, so its ``heightForWidth`` (not the one-line sizeHint) is what
    must fit (UI/UX audit 2026-10-18, UX-01 — the old width-only check let
    a 23px-tall "Choose file" button through where 34px was needed)."""
    if isinstance(widget, QLabel) and widget.wordWrap() and widget.hasHeightForWidth():
        margins = widget.contentsMargins()
        inner = widget.width() - margins.left() - margins.right()
        return widget.heightForWidth(max(1, inner)) if inner > 0 else 0
    hint = widget.sizeHint().height()
    floor = widget.minimumSizeHint().height()
    # A fixed-height widget chose its own height on purpose; trust the
    # smaller of its hint and minimum hint then.
    return min(hint, floor) if floor > 0 else hint


def vertically_clipped_widgets(window) -> list[tuple[str, int, int]]:
    """Visible text widgets shorter than the height their text needs."""
    bad: list[tuple[str, int, int]] = []
    for cls in _TEXT_WIDGET_CLASSES:
        for widget in window.findChildren(cls):
            if not widget.isVisible() or bool(widget.property("_elides")):
                continue
            text = _widget_text(widget)
            if len(text.strip()) < 3:
                continue
            needed = _needed_height(widget)
            if widget.height() < needed - 2:
                bad.append((text[:40], widget.height(), needed))
    return bad


def overlapping_layout_siblings(window) -> list[tuple[str, str]]:
    """Visible widgets managed by the same layout whose rectangles
    intersect. A QBoxLayout squeezed below its minimum does not clip — it
    lets children overlap (the start screen's icon drawn over its title).
    Deliberate overlays are not layout-managed siblings, so they never
    show up here."""
    from PyQt6.QtWidgets import QStackedLayout

    bad: list[tuple[str, str]] = []
    seen: set[int] = set()
    for widget in [window, *window.findChildren(QWidget)]:
        layout = widget.layout()
        if layout is None or id(layout) in seen or isinstance(layout, QStackedLayout):
            continue
        seen.add(id(layout))
        if not widget.isVisible():
            continue
        children = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            child = item.widget() if item is not None else None
            if child is not None and child.isVisible():
                children.append(child)
        for i, first in enumerate(children):
            for second in children[i + 1:]:
                overlap = first.geometry().intersected(second.geometry())
                if overlap.width() > 1 and overlap.height() > 1:
                    bad.append((_describe(first), _describe(second)))
    return bad


def _describe(widget) -> str:
    text = _widget_text(widget) if isinstance(widget, _TEXT_WIDGET_CLASSES) else ""
    return f"{type(widget).__name__}({widget.objectName() or text[:24]!r})"


def _check_clipping(window, state_key: str) -> None:
    """Zero tolerance (docs/UI_REDESIGN_PLAN_2026-09.ru.md, B9 — the
    per-state ui_clip_baseline.json this used to compare against is
    retired now that every state genuinely renders with none)."""
    bad = clipped_text_widgets(window)
    if bad:
        sample = "; ".join(f"{t!r} ({w}<{need})" for t, w, need in bad[:8])
        raise AssertionError(
            f"Clipped text at {state_key}: {len(bad)} widgets. Examples: {sample}"
        )
    short = vertically_clipped_widgets(window)
    if short:
        sample = "; ".join(f"{t!r} (h {h}<{need})" for t, h, need in short[:8])
        raise AssertionError(
            f"Vertically clipped text at {state_key}: {len(short)} widgets. Examples: {sample}"
        )
    overlaps = overlapping_layout_siblings(window)
    if overlaps:
        sample = "; ".join(f"{a} × {b}" for a, b in overlaps[:8])
        raise AssertionError(
            f"Overlapping layout siblings at {state_key}: {len(overlaps)}. Examples: {sample}"
        )


def _bind_demo_run(window) -> None:
    """Populate the run screen (B4) with a fixture JobRun exercising every
    status the feed can show — deterministic, touches no real engine."""
    from application.job_engine import JobRun
    from application.steps import STEP_DEFINITIONS, build_job_spec
    from domain.job import StepOutcome, StepStatus

    names = [d.name for d in STEP_DEFINITIONS]
    spec = build_job_spec("gallery-demo", names)
    run = JobRun(spec=spec)
    run.outcomes["transcribe"] = StepOutcome("transcribe", StepStatus.SUCCEEDED)
    run.outcomes["diarize"] = StepOutcome("diarize", StepStatus.SKIPPED)
    run.outcomes["clean"] = StepOutcome("clean", StepStatus.SUCCEEDED)
    run.outcomes["article"] = StepOutcome("article", StepStatus.SUCCEEDED)
    run.outcomes["insights"] = StepOutcome("insights", StepStatus.FAILED, error="LM Studio timed out")
    run.outcomes["book"] = StepOutcome("book", StepStatus.CANCELLED)
    # "youtube_package" and "cover" are left unresolved -> "waiting".
    window.run_view.bind_run(run)
    window.run_view.set_recipe_name(tr("recipe_youtube_video"))
    window.run_view.set_finished(True)


_LONG_TITLES = {
    "ru": "Интервью с главным редактором о локальной расшифровке, приватности и подготовке материалов к публикации — полная версия",
    "en": "Interview with the editor-in-chief on local transcription, privacy and preparing publication material — full version",
}


def _add_demo_record(language: str) -> int:
    """A populated record (UX-01: the audit's 1100×700 state): long
    title, two speakers, several low-confidence segments. Stored in the
    gallery's own temporary history, never the user's."""
    from domain.transcription import Segment, TranscriptionResult

    lines = {
        "ru": ("Добрый день. Сегодня говорим о том, как готовить интервью без облака.",
               "Спасибо за приглашение. Начну с того, что запись остаётся на компьютере."),
        "en": ("Good afternoon. Today we talk about preparing interviews without the cloud.",
               "Thanks for having me. First of all, the recording stays on the computer."),
    }[language]
    segments = []
    for i in range(24):
        speaker = "SPEAKER_00" if i % 2 == 0 else "SPEAKER_01"
        confidence = 0.42 if i in (3, 4, 11, 17) else 0.93
        segments.append(Segment(
            start=i * 12.0, end=i * 12.0 + 11.0, text=lines[i % 2],
            speaker=speaker, confidence=confidence,
        ))
    result = TranscriptionResult(
        segments=segments, language=language, duration=24 * 12.0,
        speaker_names={"SPEAKER_00": "Анна" if language == "ru" else "Anna",
                       "SPEAKER_01": "Борис" if language == "ru" else "Boris"},
    )
    store = history.get_history_store()
    record_id = store.add(result, source_path="", model="large-v3-turbo",
                          speaker_names=result.speaker_names,
                          source_name="interview.mp4")
    store.set_title(record_id, _LONG_TITLES[language])
    return record_id


def render(output: Path, check: bool = False) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    config.CONFIG_DIR = output
    config.CONFIG_FILE = output / "config.json"
    history._store = history.HistoryStore(output / "history.sqlite3")
    app = QApplication.instance() or QApplication(sys.argv)
    rendered: list[Path] = []

    for language in ("ru", "en"):
        for theme in ("dark", "light"):
            config._config = config.Config(
                live_transcription_enabled=True,
                ui_language=language,
                theme=theme,
            )
            load_locale(language)
            apply_theme(app, theme)
            from ui.main_window import MainWindow
            from ui.settings_dialog import SettingsDialog

            window = MainWindow()
            window.show()
            app.processEvents()
            demo_record = _add_demo_record(language)
            window.library_view.refresh()
            for width, height in SIZES:
                window.resize(width, height)
                for key in SECTIONS:
                    window._on_section_changed(key)
                    app.processEvents()
                    window.repaint()
                    app.processEvents()
                    state_key = f"{language}-{theme}-{width}x{height}-{key}"
                    path = output / f"{state_key}.png"
                    if not window.grab().save(str(path)):
                        raise RuntimeError(f"Could not render {path}")
                    rendered.append(path)
                    if check:
                        for widget in window.findChildren(type(window.centralWidget())):
                            if widget.isVisible() and widget.minimumWidth() > 0:
                                if widget.width() <= 0 or widget.height() <= 0:
                                    raise AssertionError(f"Invalid geometry: {widget.objectName()}")
                        if key == "library" and not _inside(
                            window, window.file_selector.browse_btn
                        ):
                            raise AssertionError(
                                f"File browse action is clipped at {width}x{height}"
                            )
                        _check_clipping(window, state_key)
                window._load_from_history(demo_record)
                window._stack.setCurrentIndex(window._record_index)
                window.status_bar.show_queue(False)
                app.processEvents()
                record_key = f"{language}-{theme}-{width}x{height}-record"
                path = output / f"{record_key}.png"
                window.grab().save(str(path))
                rendered.append(path)
                if check:
                    _check_clipping(window, record_key)

                _bind_demo_run(window)
                window._stack.setCurrentIndex(window._run_index)
                app.processEvents()
                run_key = f"{language}-{theme}-{width}x{height}-run"
                path = output / f"{run_key}.png"
                window.grab().save(str(path))
                rendered.append(path)
                if check:
                    _check_clipping(window, run_key)

                window.command_palette.open_palette()
                app.processEvents()
                path = output / f"{language}-{theme}-{width}x{height}-palette.png"
                window.command_palette.grab().save(str(path))
                rendered.append(path)
                window.command_palette.reject()

            settings = SettingsDialog(window)
            settings.resize(840, 680)
            settings.show()
            app.processEvents()
            path = output / f"{language}-{theme}-settings.png"
            settings.grab().save(str(path))
            rendered.append(path)
            if check and not all(
                _inside(settings, widget)
                for widget in (settings._categories, settings._pages)
            ):
                raise AssertionError("Settings content is outside its viewport")
            if check:
                _check_clipping(settings, f"{language}-{theme}-settings")
            settings.close()
            window.close()
            app.processEvents()
            history.get_history_store().delete(demo_record)
    return rendered


def main() -> int:
    args = _parse_args()
    output = args.output or Path(tempfile.gettempdir()) / "whispered-ui-gallery"
    rendered = render(output, check=args.check)
    print(f"Rendered {len(rendered)} states to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
