"""Editable controls for a cover preview."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from covers.renderer import MAX_PHOTO_ZOOM

from ui.i18n_helpers import Retranslator
from ui.option_labels import COVER_VARIANT_CHOICES

# Discrete focal points for cropping a photo slot (normalised 0..1), in the
# same spirit as CSS ``object-position``. Keeps the crop control to a combo
# box — no extra dialog or drag handling.
_FOCUS_CHOICES: list[tuple[str, tuple[float, float]]] = [
    ("cover_focus_center", (0.5, 0.5)),
    ("cover_focus_top", (0.5, 0.15)),
    ("cover_focus_bottom", (0.5, 0.85)),
    ("cover_focus_left", (0.15, 0.5)),
    ("cover_focus_right", (0.85, 0.5)),
]


class PhotoFraming(QWidget):
    """Focal point + zoom for one photo slot: the focus combo and a 100…250 %
    slider that enlarges the cover-fit crop around that point — enough to
    frame a small face from a video-call tile. Shared by the Cover
    workspace inspector and the YouTube publish wizard's cover step."""

    framing_changed = pyqtSignal(float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._i18n = Retranslator()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.focus_combo = QComboBox()
        self._i18n.combo_items(self.focus_combo, _FOCUS_CHOICES)
        row.addWidget(self.focus_combo)
        row.addWidget(self._i18n.text(QLabel(), "cover_zoom"))
        self.zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.zoom_slider.setRange(100, round(MAX_PHOTO_ZOOM * 100))
        self.zoom_slider.setSingleStep(5)
        self.zoom_slider.setPageStep(25)
        self.zoom_slider.setValue(100)
        row.addWidget(self.zoom_slider, 1)
        self.zoom_label = QLabel()
        self.zoom_label.setMinimumWidth(40)
        row.addWidget(self.zoom_label)
        # A focal point that is not one of the presets (set from a face,
        # see covers/speaker_photos.framing_for) — kept while only the zoom
        # changes, dropped once a preset is picked.
        self._custom_focus: tuple[float, float] | None = None
        self._i18n.bind()
        self._show_zoom()
        self.focus_combo.currentIndexChanged.connect(self._on_focus_picked)
        self.zoom_slider.valueChanged.connect(self._emit)

    def framing(self) -> tuple[tuple[float, float], float]:
        fx, fy = self._custom_focus or self.focus_combo.currentData()
        return (fx, fy), self.zoom_slider.value() / 100

    def _on_focus_picked(self, *_args) -> None:
        self._custom_focus = None
        self._emit()

    def set_framing(self, focus: tuple[float, float], zoom: float) -> None:
        """Show *focus*/*zoom* without emitting ``framing_changed``; a focal
        point that isn't one of the presets leaves the combo where it is and
        is kept until a preset is picked."""
        index = next(
            (i for i, (_key, point) in enumerate(_FOCUS_CHOICES) if point == tuple(focus)),
            -1,
        )
        for widget in (self.focus_combo, self.zoom_slider):
            widget.blockSignals(True)
        if index >= 0:
            self.focus_combo.setCurrentIndex(index)
        self._custom_focus = None if index >= 0 else (float(focus[0]), float(focus[1]))
        self.zoom_slider.setValue(round(zoom * 100))
        for widget in (self.focus_combo, self.zoom_slider):
            widget.blockSignals(False)
        self._show_zoom()

    def _show_zoom(self) -> None:
        self.zoom_label.setText(f"{self.zoom_slider.value()} %")

    def _emit(self, *_args) -> None:
        self._show_zoom()
        (fx, fy), zoom = self.framing()
        self.framing_changed.emit(fx, fy, zoom)


class CoverInspector(QWidget):
    changed = pyqtSignal()
    choose_photo = pyqtSignal(str)
    grab_frame = pyqtSignal(str)
    suggest_photos = pyqtSignal(str)
    # slot, focus_x, focus_y, zoom
    framing_changed = pyqtSignal(str, float, float, float)
    export_requested = pyqtSignal()
    suggest_requested = pyqtSignal()
    shuffle_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._i18n = Retranslator()
        self._video_available = False
        self._frame_buttons: list = []
        self.framing: dict[str, PhotoFraming] = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.layout_combo = QComboBox()
        self._i18n.combo_items(self.layout_combo, [
            ("cover_layout_duo", "duo"),
            ("cover_layout_solo", "solo"),
            ("cover_layout_text", "text_only"),
        ])
        self.variant_combo = QComboBox()
        self._i18n.combo_items(self.variant_combo, COVER_VARIANT_CHOICES)
        self.title_edit = QPlainTextEdit()
        self.title_edit.setMaximumHeight(100)
        self.names_edit = QLineEdit()
        self._i18n.form_row(form, "cover_layout", self.layout_combo)
        self._i18n.form_row(form, "cover_variant", self.variant_combo)
        self._i18n.form_row(form, "cover_title", self.title_edit)
        self._i18n.form_row(form, "cover_names", self.names_edit)
        layout.addLayout(form)
        self.shuffle_button = self._i18n.text(
            QPushButton(), "cover_shuffle", tooltip="cover_shuffle_tip"
        )
        self.shuffle_button.clicked.connect(self.shuffle_requested.emit)
        layout.addWidget(self.shuffle_button)
        self.suggest_button = self._i18n.text(QPushButton(), "cover_suggest_title")
        self.suggest_button.clicked.connect(self.suggest_requested.emit)
        layout.addWidget(self.suggest_button)
        for slot in ("photo_a", "photo_b"):
            row = QHBoxLayout()
            button = self._i18n.text(QPushButton(), "cover_choose_photo")
            button.clicked.connect(
                lambda _checked=False, name=slot: self.choose_photo.emit(name)
            )
            row.addWidget(button)
            frame_button = self._i18n.text(QPushButton(), "cover_frame_from_video")
            frame_button.setEnabled(False)
            frame_button.clicked.connect(
                lambda _checked=False, name=slot: self.grab_frame.emit(name)
            )
            self._frame_buttons.append(frame_button)
            row.addWidget(frame_button)
            variants_button = self._i18n.text(
                QPushButton(), "cover_photo_variants", tooltip="cover_photo_variants_tip"
            )
            variants_button.setEnabled(False)
            variants_button.clicked.connect(
                lambda _checked=False, name=slot: self.suggest_photos.emit(name)
            )
            self._frame_buttons.append(variants_button)
            row.addWidget(variants_button)
            layout.addLayout(row)
            framing = PhotoFraming()
            framing.framing_changed.connect(
                lambda fx, fy, zoom, name=slot: self.framing_changed.emit(
                    name, fx, fy, zoom
                )
            )
            self.framing[slot] = framing
            layout.addWidget(framing)
        self.export_button = self._i18n.text(QPushButton(), "cover_export")
        self.export_button.setProperty("variant", "primary")
        self.export_button.clicked.connect(self.export_requested.emit)
        layout.addWidget(self.export_button)
        layout.addStretch()
        self._i18n.bind()
        self.layout_combo.currentIndexChanged.connect(self.changed.emit)
        self.variant_combo.currentIndexChanged.connect(self.changed.emit)
        self.title_edit.textChanged.connect(self.changed.emit)
        self.names_edit.textChanged.connect(self.changed.emit)

    def set_video_available(self, available: bool) -> None:
        """Enable the per-slot 'frame from video' buttons only when the
        loaded source is a video FFmpeg can pull stills from."""
        self._video_available = available
        for button in self._frame_buttons:
            button.setEnabled(available)

    def state(self) -> tuple[str, str, dict[str, str]]:
        return (
            self.layout_combo.currentData(),
            self.variant_combo.currentData(),
            {"title": self.title_edit.toPlainText(), "names": self.names_edit.text()},
        )
