"""Cover-generator workspace with debounced live preview."""

from __future__ import annotations

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

from config import get_config
from core.i18n import tr
from core.insights_worker import InsightsWorker
from core.worker_registry import WorkerRegistry
from covers.export import export
from covers.renderer import render
from covers.template import load_template
from ui.cover_inspector import CoverInspector


class CoverView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.template = load_template(get_config().cover_template)
        self.photos: dict[str, str] = {}
        self.last_image = None
        self._workers: list = []
        self._registry = WorkerRegistry(parent=self)
        self._segments = []
        root = QHBoxLayout(self)
        preview_column = QVBoxLayout()
        title = QLabel(tr("cover_workspace_title"))
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
        self.inspector.export_requested.connect(self._export)
        self.inspector.suggest_requested.connect(self._suggest_title)
        self.render_preview()

    def _choose_photo(self, slot: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("cover_choose_photo"), "", "Images (*.png *.jpg *.jpeg)"
        )
        if path:
            self.photos[slot] = path
            self.render_preview()

    def set_segments(self, segments) -> None:
        self._segments = list(segments or [])

    def _suggest_title(self) -> None:
        if not self._segments:
            self.warning.setText(tr("cover_no_transcript"))
            return
        worker = InsightsWorker(
            "thumb_title", self._segments, get_config().lm_studio_url, parent=self
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

    def render_preview(self) -> None:
        layout, variant, slots = self.inspector.state()
        slots.update(self.photos)
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
            slots.update(self.photos)
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
        QMessageBox.information(
            self, tr("cover_export"), tr("cover_export_done", count=len(files))
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
        self._workers.clear()
        self._registry.shutdown_all(timeout_ms=timeout)
