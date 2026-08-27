"""Cover-generator workspace with debounced live preview."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from application.artifact_provenance import source_fingerprint, transcript_revision
from config import get_config
from core.i18n import tr
from ui.i18n_helpers import Retranslator
from core.insights_worker import InsightsWorker
from core.logger import get_logger
from core.prompts import prompt_version
from core.worker_registry import WorkerRegistry
from covers.export import export
from covers.renderer import render
from covers.template import load_template
from domain.artifact import Artifact
from infrastructure.persistence import artifact_store
from ui.cover_inspector import CoverInspector
from ui.cover_frame_dialog import CoverFrameDialog, FrameGrabWorker

logger = get_logger(__name__)

# Container formats we can pull still frames from with FFmpeg. Kept local
# rather than reusing utils.SUPPORTED_FORMATS so audio-only sources never
# light up the "frame from video" controls.
_VIDEO_EXTS = frozenset(
    {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".m4v"}
)


class CoverView(QWidget):
    def __init__(self, parent=None, insights_cache=None):
        super().__init__(parent)
        self.template = load_template(get_config().cover_template)
        self.photos: dict[str, str] = {}
        # Per-slot focal point (normalised 0..1) for cover-fit cropping.
        self._focus: dict[str, tuple[float, float]] = {}
        self.last_image = None
        self._workers: list = []
        self._registry = WorkerRegistry(parent=self)
        # Shared with YouTube/Insights panels by MainWindow — see
        # core/insights_cache.py. thumb_title is a distinct insight_type
        # so it never collides with their cache entries; this just avoids
        # recomputing a title suggestion for the exact same transcript.
        self._insights_cache = insights_cache
        self._segments = []
        # Set via set_provenance() by MainWindow whenever the open
        # transcript changes — recorded into each export's Artifact
        # manifest (see infrastructure/persistence/artifact_store.py).
        self._record_id: int | None = None
        self._source_path: str | None = None
        self._transcript_language = ""
        # Set via set_video_source()/set_playhead() by MainWindow so a
        # photo slot can be filled from a still of the loaded video rather
        # than an external file (see _grab_frame).
        self._video: str | None = None
        self._playhead: float = 0.0
        # Lazily created scratch dir for stills pulled from the video;
        # removed in shutdown().
        self._frame_dir: str | None = None
        self._i18n = Retranslator()
        root = QHBoxLayout(self)
        preview_column = QVBoxLayout()
        title = self._i18n.text(QLabel(), "cover_workspace_title")
        title.setProperty("role", "section-title")
        preview_column.addWidget(title)
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 220)
        self.preview.setProperty("role", "card")
        preview_column.addWidget(self.preview, stretch=1)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setProperty("role", "muted")
        preview_column.addWidget(self.warning)
        root.addLayout(preview_column, stretch=1)
        self.inspector = CoverInspector()
        root.addWidget(self.inspector)
        cfg = get_config()
        self.inspector.title_edit.setPlainText("")
        self.inspector.names_edit.setText(cfg.cover_host_name)
        if cfg.cover_host_photo:
            self.photos["photo_a"] = cfg.cover_host_photo
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.render_preview)
        self.inspector.changed.connect(lambda: self._timer.start())
        self.inspector.choose_photo.connect(self._choose_photo)
        self.inspector.grab_frame.connect(self._grab_frame)
        self.inspector.focus_changed.connect(self._on_focus_changed)
        self.inspector.export_requested.connect(self._export)
        self.inspector.suggest_requested.connect(self._suggest_title)
        self._i18n.bind()
        self.render_preview()

    def _choose_photo(self, slot: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("cover_choose_photo"), "", "Images (*.png *.jpg *.jpeg)"
        )
        if path:
            self.photos[slot] = path
            self.render_preview()

    def set_segments(self, segments, transcript_language: str | None = None) -> None:
        self._segments = list(segments or [])
        self._transcript_language = transcript_language or ""

    def _grab_frame(self, slot: str) -> None:
        if not self._video:
            return
        try:
            from video_input import probe_video

            _, duration = probe_video(self._video)
        except Exception:
            duration = 0.0
        if self._frame_dir is None:
            self._frame_dir = tempfile.mkdtemp(prefix="whispered-cover-frames-")
        dialog = CoverFrameDialog(
            self._video, self._playhead, duration, self._frame_dir, parent=self
        )
        if dialog.exec() != CoverFrameDialog.DialogCode.Accepted:
            return
        worker = FrameGrabWorker(
            self._video, dialog.selected_time, self._frame_dir, parent=self
        )
        worker.ready.connect(lambda path, s=slot: self._on_frame_ready(s, path))
        worker.failed.connect(lambda message: self.warning.setText(message))
        for signal in (worker.ready, worker.failed):
            signal.connect(
                lambda *_a, w=worker: self._workers.remove(w)
                if w in self._workers
                else None
            )
        self._workers.append(worker)
        self._registry.register(worker, name=f"cover_frame_{id(worker)}")
        self.warning.setText(tr("cover_frame_extracting"))
        worker.start()

    def _on_focus_changed(self, slot: str, fx: float, fy: float) -> None:
        self._focus[slot] = (fx, fy)
        self.render_preview()

    def _photo_slots(self) -> dict[str, object]:
        """Merge chosen photo paths with their focal point so the renderer
        crops toward it (a plain path stays a plain path)."""
        merged: dict[str, object] = {}
        for slot, path in self.photos.items():
            focus = self._focus.get(slot)
            if focus and focus != (0.5, 0.5):
                merged[slot] = {
                    "file": path, "focus_x": focus[0], "focus_y": focus[1]
                }
            else:
                merged[slot] = path
        return merged

    def _on_frame_ready(self, slot: str, path: str) -> None:
        self.photos[slot] = path
        self.warning.clear()
        self.render_preview()

    def set_video_source(self, path: str | None) -> None:
        """Called by MainWindow when the loaded media changes. A non-video
        source (audio, transcript-only) clears the frame-grab controls."""
        self._video = (
            path if path and Path(path).suffix.lower() in _VIDEO_EXTS else None
        )
        self.inspector.set_video_available(self._video is not None)

    def set_playhead(self, seconds: float) -> None:
        """Track the player position so 'current frame' can grab it."""
        self._playhead = max(0.0, float(seconds))

    def set_provenance(self, record_id: int | None, source_path: str | None) -> None:
        """Called by MainWindow whenever the open transcript's identity
        changes (fresh transcription, history load, or a save that first
        assigns a record id) — recorded into each export's Artifact
        manifest so a cover file can answer "which transcript/source
        produced this" later."""
        self._record_id = record_id
        self._source_path = source_path

    def _suggest_title(self) -> None:
        if not self._segments:
            self.warning.setText(tr("cover_no_transcript"))
            return
        worker = InsightsWorker(
            "thumb_title", self._segments, get_config().lm_studio_url, parent=self,
            cache=self._insights_cache,
        )
        worker.finished.connect(self._on_title_suggestions)
        worker.error_occurred.connect(
            lambda _kind, message: self.warning.setText(message)
        )
        worker.finished.connect(
            lambda *_args: self._workers.remove(worker)
            if worker in self._workers
            else None
        )
        self._workers.append(worker)
        self._registry.register(worker, name=f"cover_title_{id(worker)}")
        worker.start()

    def _on_title_suggestions(self, _kind, suggestions) -> None:
        if suggestions:
            self.inspector.title_edit.setPlainText(suggestions[0].text)
            self.warning.setText(" · ".join(suggestions[0].warnings))

    def render_params(self) -> dict:
        """This workspace's current selections as ``application/steps.py``'s
        ``cover_*`` StepContext params.

        A recipe that includes the "cover" step (e.g. the built-in
        "YouTube video" one) renders through the same template/layout/
        variant/slots the user set up here — without this, the step's
        runner falls back to its own hardcoded defaults and silently
        ignores what they chose.
        """
        layout, variant, slots = self.inspector.state()
        slots.update(self._photo_slots())
        return {
            "cover_template": self.template.id,
            "cover_layout": layout,
            "cover_variant": variant,
            "cover_slots": slots,
        }

    def render_preview(self) -> None:
        layout, variant, slots = self.inspector.state()
        slots.update(self._photo_slots())
        try:
            self.last_image, warnings = render(
                self.template, layout, variant, slots, (1280, 720)
            )
            pixmap = QPixmap.fromImage(self.last_image)
            self.preview.setPixmap(
                pixmap.scaled(
                    self.preview.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.warning.setText(" · ".join(dict.fromkeys(warnings)))
        except Exception as exc:
            self.warning.setText(str(exc))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._timer.start()

    def _export(self) -> None:
        if self.last_image is None:
            return
        directory = QFileDialog.getExistingDirectory(self, tr("cover_export"))
        if not directory:
            return
        try:
            layout, variant, slots = self.inspector.state()
            slots.update(self._photo_slots())
            cfg = get_config()
            shorts_image = None
            if cfg.cover_export_shorts:
                vertical = load_template("prosvet_9x16")
                shorts_image, shorts_warnings = render(
                    vertical, layout, variant, slots, (1080, 1920)
                )
                if shorts_warnings:
                    self.warning.setText(" · ".join(dict.fromkeys(shorts_warnings)))
            files = export(
                self.last_image,
                shorts_image,
                Path(directory),
                slots.get("title") or "cover",
                state={
                    "template": self.template.id,
                    "layout": layout,
                    "variant": variant,
                    "slots": slots,
                },
                jpeg_max_bytes=cfg.cover_jpeg_max_bytes,
                export_shorts=cfg.cover_export_shorts,
            )
        except Exception as exc:
            QMessageBox.critical(self, tr("cover_export"), str(exc))
            return
        self._write_provenance(files)
        QMessageBox.information(
            self, tr("cover_export"), tr("cover_export_done", count=len(files))
        )

    def _write_provenance(self, files: list[Path]) -> None:
        """Record an Artifact manifest for this export's PNG (see
        docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R5-full step 3) — answers
        "which transcript revision and source produced this file" later.
        Best-effort: the PNG/JPEG/sidecar are already safely written by
        the time this runs, so a manifest failure must not turn a
        successful export into a reported failure.

        provider/model/prompt_version match application/steps.py's "cover"
        step (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5f) rather than
        this export flow running through JobRunner itself — cover's render
        is a synchronous, local QPainter call with no LLM involved (unlike
        the other five generators), so there is no worker to migrate here;
        this closes the same "empty provider/model/prompt_version" gap B0
        already fixed for the step's own artifact writer, which this
        interactive export path never shared.
        """
        png = next((f for f in files if f.suffix == ".png" and "-shorts" not in f.name), None)
        if png is None:
            return
        try:
            artifact = Artifact(
                record_id=str(self._record_id) if self._record_id is not None else "unsaved",
                source_hash=source_fingerprint(self._source_path),
                source_path=self._source_path or "",
                transcript_revision=transcript_revision(self._segments, self._transcript_language),
                type="cover",
                path=str(png),
                provider="lmstudio",
                prompt_version=prompt_version("thumb_title"),
            )
            artifact_store.save(artifact)
        except Exception as exc:
            logger.warning("Failed to write cover artifact manifest for %s: %s", png, exc)

    def shutdown(self, timeout: int = 2000) -> None:
        """Part of the Shutdownable protocol (ui/shutdownable.py).

        Retiring through WorkerRegistry — rather than a bare cancel()+
        wait(timeout) per worker — disconnects each worker's business
        signals before waiting (a title-suggestion result arriving after
        the window starts closing must not still write into
        self.inspector) and keeps any worker that outlives the bounded
        wait alive until its QThread actually finishes, instead of leaving
        it referenced with no further supervision.
        """
        self._timer.stop()
        self._workers.clear()
        self._registry.shutdown_all(timeout_ms=timeout)
        if self._frame_dir:
            shutil.rmtree(self._frame_dir, ignore_errors=True)
            self._frame_dir = None
