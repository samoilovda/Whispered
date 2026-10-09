"""Cover-generator workspace with debounced live preview."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from application import cover_setup
from application.cover_setup import CoverSetup, PhotoSetup
from config import get_config
from core.i18n import tr
from ui.i18n_helpers import Retranslator
from core.insights_worker import InsightsWorker
from core.logger import get_logger
from application.artifacts import record_export
from core.prompts import prompt_version
from core.worker_registry import WorkerRegistry
from covers.export import export
from covers.renderer import render
from covers.style import pick_style
from covers.template import load_template
from ui.cover_inspector import CoverInspector
from ui.cover_frame_dialog import CoverFrameDialog, FrameGrabWorker

logger = get_logger(__name__)

# Container formats we can pull still frames from with FFmpeg. Kept local
# rather than reusing utils.SUPPORTED_FORMATS so audio-only sources never
# light up the "frame from video" controls.
_VIDEO_EXTS = frozenset(
    {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".m4v"}
)


def _style_seed(slots: dict, source_path: str | None) -> str:
    """What "auto" styling is seeded by: the title, or — before one is
    typed — the source file name, so untitled episodes still differ.
    application/steps.py's cover step uses the same rule."""
    title = str(slots.get("title") or "").strip()
    return title or (Path(source_path).stem if source_path else "")


class CoverView(QWidget):
    # The freshly rendered 1280x720 preview (QImage) — the YouTube publish
    # wizard shows it while the user tunes the cover there.
    preview_changed = pyqtSignal(object)

    def __init__(self, parent=None, insights_cache=None):
        super().__init__(parent)
        self.template = load_template(get_config().cover_template)
        self.photos: dict[str, str] = {}
        # Per-slot focal point (normalised 0..1) and zoom (1.0 = plain
        # cover fit) for cropping a photo.
        self._focus: dict[str, tuple[float, float]] = {}
        self._zoom: dict[str, float] = {}
        self.last_image = None
        # How many times "Shuffle" was pressed; with the title it seeds the
        # "auto" palette/leaf pick (covers/style.py).
        self._shuffle = 0
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
        # The setup last written to the record's cover.setup.json
        # (application/cover_setup.py), so an unchanged render does not
        # rewrite it.
        self._saved_setup: CoverSetup | None = None
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
        # The Settings dialog's default layout/variant — without this the
        # combos always opened on their first item whatever was saved.
        for combo, value in (
            (self.inspector.layout_combo, cfg.cover_layout),
            (self.inspector.variant_combo, cfg.cover_variant),
        ):
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)
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
        self.inspector.suggest_photos.connect(self._suggest_photos)
        self.inspector.framing_changed.connect(self._on_framing_changed)
        self.inspector.export_requested.connect(self._export)
        self.inspector.suggest_requested.connect(self._suggest_title)
        self.inspector.shuffle_requested.connect(self._on_shuffle)
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

    def _suggest_photos(self, slot: str) -> None:
        """Fill *slot* from the prepared speaker photos of the video
        (covers.speaker_photos), framed so the face sits like in the other
        slot."""
        if not self._video:
            return
        from ui.cover_candidates_dialog import SpeakerPhotoDialog, candidate_dir

        try:
            from video_input import probe_video

            _, duration = probe_video(self._video)
        except Exception:
            duration = 0.0
        if self._frame_dir is None:
            self._frame_dir = tempfile.mkdtemp(prefix="whispered-cover-frames-")
        dialog = SpeakerPhotoDialog(
            self._video, duration, candidate_dir(self._artifact_dir(), self._frame_dir),
            registry=self._registry, parent=self,
        )
        if dialog.exec() != SpeakerPhotoDialog.DialogCode.Accepted or dialog.selected is None:
            return
        self.apply_photo_candidate(slot, dialog.selected)

    def apply_photo_candidate(self, slot: str, candidate) -> None:
        """Put a ``PhotoCandidate`` into *slot* with face-based framing."""
        from PyQt6.QtGui import QImage

        from covers.speaker_photos import framing_for, slot_size

        self.photos[slot] = candidate.path
        layout = self.inspector.layout_combo.currentData() or ""
        box = slot_size(self.template, layout, slot)
        image = QImage(candidate.path)
        if box is not None and not image.isNull():
            focus, zoom = framing_for(candidate.face, box, (image.width(), image.height()))
        else:
            focus, zoom = (0.5, 0.5), 1.0
        self._focus[slot] = focus
        self._zoom[slot] = zoom
        framing = self.inspector.framing.get(slot)
        if framing is not None:
            framing.set_framing(focus, zoom)
        self.render_preview()

    def _on_framing_changed(
        self, slot: str, fx: float, fy: float, zoom: float
    ) -> None:
        self._focus[slot] = (fx, fy)
        self._zoom[slot] = zoom
        self.render_preview()

    def _photo_slots(self) -> dict[str, object]:
        """Merge chosen photo paths with their focal point and zoom so the
        renderer crops toward it (a plain path stays a plain path, and an
        unzoomed slot carries no ``zoom`` key — its cache key is unchanged)."""
        merged: dict[str, object] = {}
        for slot, path in self.photos.items():
            focus = self._focus.get(slot) or (0.5, 0.5)
            zoom = self._zoom.get(slot, 1.0)
            if focus == (0.5, 0.5) and zoom == 1.0:
                merged[slot] = path
                continue
            value: dict[str, object] = {
                "file": path, "focus_x": focus[0], "focus_y": focus[1]
            }
            if zoom != 1.0:
                value["zoom"] = zoom
            merged[slot] = value
        return merged

    def _on_frame_ready(self, slot: str, path: str) -> None:
        self.photos[slot] = self._pick_speaker(slot, path)
        self.warning.clear()
        self.render_preview()

    def _pick_speaker(self, slot: str, path: str) -> str:
        """A still from a video call shows everyone: when it holds several
        participants, ask whose picture this slot gets and keep just that
        crop. A single view (or "whole frame") keeps the still as is."""
        from PyQt6.QtGui import QImage

        from ui.cover_tile_dialog import SpeakerTileDialog, save_crop, speaker_crops

        image = QImage(path)
        tiles = speaker_crops(image)
        if len(tiles) < 2:
            return path
        dialog = SpeakerTileDialog(image, tiles, parent=self)
        if dialog.exec() != SpeakerTileDialog.DialogCode.Accepted or dialog.selected is None:
            return path
        target = Path(path).with_name(f"{Path(path).stem}_{slot}.png")
        return str(save_crop(image, tiles[dialog.selected], target))

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
        produced this" later.

        Another record starts from a clean cover, then gets back whatever
        cover setup it had (application/cover_setup.py) — also after a
        restart. A save that first assigns an id keeps the current setup."""
        previous_source, previous_id = self._source_path, self._record_id
        switched = (bool(previous_source) and source_path != previous_source) or (
            previous_id is not None and record_id != previous_id
        )
        if switched:
            # Not yet rendered edits (the preview is debounced) belong to
            # the record being left.
            self._save_setup()
        self._record_id = record_id
        self._source_path = source_path
        if switched:
            self._reset_episode()
        if record_id != previous_id:
            self._saved_setup = None
            art_dir = self._artifact_dir()
            setup = cover_setup.load_setup(art_dir) if art_dir is not None else None
            if setup is not None:
                self._apply_setup(setup)
        # An untitled cover's "auto" style is seeded by the source name.
        self._timer.start()

    def _artifact_dir(self) -> Path | None:
        """The open record's output folder (core.paths.artifact_dir, named
        like MainWindow's), or ``None`` before it has a record id."""
        if self._record_id is None:
            return None
        from core.paths import artifact_dir

        try:
            return artifact_dir(self._record_id, self._source_path or "recording")
        except ValueError as exc:
            logger.warning("No artifact folder for the cover setup: %s", exc)
            return None

    def _current_setup(self, art_dir: Path) -> CoverSetup:
        """What is on screen now, with picked/grabbed photos copied into the
        record's folder (and pointed at there, so the next render — and the
        cover step's cache key — uses the kept copy)."""
        host_photo = get_config().cover_host_photo
        layout, variant, slots = self.inspector.state()
        photos: dict[str, PhotoSetup] = {}
        for slot in cover_setup.PHOTO_SLOTS:
            path = self.photos.get(slot)
            if not path:
                continue
            if path == host_photo and slot == "photo_a":
                kept: str | None = None
            else:
                kept = cover_setup.store_photo(art_dir, slot, path)
                self.photos[slot] = kept
            focus, zoom = self.photo_framing(slot)
            photos[slot] = PhotoSetup(kept, focus, zoom)
        return CoverSetup(
            layout=layout or "",
            variant=variant or "",
            shuffle=self._shuffle,
            title=slots.get("title", ""),
            names=slots.get("names", ""),
            photos=photos,
        )

    def _save_setup(self) -> None:
        """Keep the open record's cover setup (no-op before it has an id)."""
        art_dir = self._artifact_dir()
        if art_dir is None:
            return
        setup = self._current_setup(art_dir)
        if setup != self._saved_setup:
            cover_setup.save_setup(art_dir, setup)
            self._saved_setup = setup

    def _apply_setup(self, setup: CoverSetup) -> None:
        """Show a stored setup. Without a stored ``photo_a`` the host photo
        from Settings stays; without ``photo_b`` the slot is empty."""
        for combo, value in (
            (self.inspector.layout_combo, setup.layout),
            (self.inspector.variant_combo, setup.variant),
        ):
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)
        self.inspector.title_edit.setPlainText(setup.title)
        self.inspector.names_edit.setText(setup.names)
        self._shuffle = setup.shuffle
        host_photo = get_config().cover_host_photo
        for slot in cover_setup.PHOTO_SLOTS:
            photo = setup.photos.get(slot)
            path = None if photo is None else photo.path or host_photo
            if path is None and slot == "photo_a":
                path = host_photo
            if path:
                self.photos[slot] = path
            else:
                self.photos.pop(slot, None)
            focus, zoom = (photo.focus, photo.zoom) if photo else ((0.5, 0.5), 1.0)
            self._focus[slot] = focus
            self._zoom[slot] = zoom
            self.inspector.framing[slot].set_framing(focus, zoom)
        self._saved_setup = setup

    def _reset_episode(self) -> None:
        """Drop what belonged to the previous recording — its title, guest
        name and photos with their framing, shuffle count — so another
        episode's cover never goes out with them. The host's name/photo
        from Settings stay."""
        cfg = get_config()
        self._shuffle = 0
        for slot in cover_setup.PHOTO_SLOTS:
            self.photos.pop(slot, None)
            self._focus.pop(slot, None)
            self._zoom.pop(slot, None)
            self.inspector.framing[slot].set_framing((0.5, 0.5), 1.0)
        if cfg.cover_host_photo:
            self.photos["photo_a"] = cfg.cover_host_photo
        self.inspector.title_edit.setPlainText("")
        self.inspector.names_edit.setText(cfg.cover_host_name)

    # ----------------------------------------------- publish-wizard API

    def set_cover_texts(self, title: str, host: str, guest: str) -> None:
        """Title and speakers as agreed in the publish wizard. A guest
        switches to the two-person layout, no guest to the solo one."""
        from covers.title import join_speakers

        names = join_speakers(host, guest, self._transcript_language)
        self.inspector.title_edit.setPlainText(title)
        self.inspector.names_edit.setText(names)
        index = self.inspector.layout_combo.findData("duo" if guest else "solo")
        if index >= 0:
            self.inspector.layout_combo.setCurrentIndex(index)
        self.render_preview()

    def shuffle(self) -> None:
        """Next palette/leaf combination (the "Shuffle" button)."""
        self._on_shuffle()

    def choose_photo(self, slot: str) -> None:
        self._choose_photo(slot)

    def grab_frame(self, slot: str) -> None:
        self._grab_frame(slot)

    def has_video(self) -> bool:
        return bool(self._video)

    def photo_framing(self, slot: str) -> tuple[tuple[float, float], float]:
        """The slot's focal point and zoom, as the wizard's controls show them."""
        return self._focus.get(slot) or (0.5, 0.5), self._zoom.get(slot, 1.0)

    def set_photo_framing(
        self, slot: str, focus: tuple[float, float], zoom: float
    ) -> None:
        """Framing chosen in the publish wizard; mirrored into this
        workspace's inspector so both show the same crop."""
        self.inspector.framing[slot].set_framing(focus, zoom)
        self._on_framing_changed(slot, focus[0], focus[1], zoom)

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

    def _on_shuffle(self) -> None:
        self._shuffle += 1
        self.render_preview()

    def _state(self) -> tuple[str, str, str | None, dict]:
        """Layout, concrete variant, decor set and slots as rendered now —
        ``"auto"`` resolved once here so the preview, the export and the
        recipe's cover step all draw the same cover."""
        layout, variant, slots = self.inspector.state()
        slots.update(self._photo_slots())
        variant, decor_set = pick_style(
            list(self.template.variants), list(self.template.decor_sets),
            variant=variant, title=_style_seed(slots, self._source_path),
            shuffle=self._shuffle,
        )
        return layout, variant, decor_set, slots

    def render_params(self) -> dict:
        """This workspace's current selections as ``application/steps.py``'s
        ``cover_*`` StepContext params.

        A recipe that includes the "cover" step (e.g. the built-in
        "YouTube video" one) renders through the same template/layout/
        variant/slots the user set up here — without this, the step's
        runner falls back to its own hardcoded defaults and silently
        ignores what they chose.
        """
        layout, variant, decor_set, slots = self._state()
        return {
            "cover_template": self.template.id,
            "cover_layout": layout,
            "cover_variant": variant,
            "cover_decor_set": decor_set,
            "cover_slots": slots,
        }

    def render_preview(self) -> None:
        # First, so a just picked photo is drawn from the record's kept copy.
        self._save_setup()
        try:
            layout, variant, decor_set, slots = self._state()
            self.last_image, warnings = render(
                self.template, layout, variant, slots, (1280, 720), decor_set
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
            self.preview_changed.emit(self.last_image)
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
            layout, variant, decor_set, slots = self._state()
            cfg = get_config()
            shorts_image = None
            if cfg.cover_export_shorts:
                vertical = load_template("prosvet_9x16")
                # The vertical template has its own (fixed) leaves.
                shorts_image, shorts_warnings = render(
                    vertical, layout, variant, slots, (1080, 1920),
                    decor_set if decor_set in vertical.decor_sets else None,
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
                    "decor_set": decor_set,
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
        record_export(
            record_id=self._record_id,
            source_path=self._source_path,
            segments=self._segments,
            language=self._transcript_language,
            type="cover",
            path=png,
            provider="lmstudio",
            prompt_version=prompt_version("thumb_title"),
        )

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
        self._save_setup()
        self._workers.clear()
        self._registry.shutdown_all(timeout_ms=timeout)
        if self._frame_dir:
            shutil.rmtree(self._frame_dir, ignore_errors=True)
            self._frame_dir = None
