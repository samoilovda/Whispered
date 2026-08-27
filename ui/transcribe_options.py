"""Whispered - Transcribe Options
Model / language / translate / performance / diarization controls.

Since the workspace redesign these are only reachable through the recipe
editor ("Настроить…" on the start screen, see ui/recipe_editor.py) — the
header they used to live in, and the LaunchBar popover that replaced it,
are both retired. That made persistence load-bearing: it is now the only
place a user picks a Whisper model outside Settings, so a selection made
here is written straight back to Config instead of living for the
session and silently reverting to Settings' defaults on the next launch.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
)
from PyQt6.QtCore import pyqtSignal

from config import get_config, save_config
from core.i18n import tr
from ui.components import ElidingComboBox
from ui.option_labels import (
    model_state_suffix,
    performance_mode_options,
    whisper_language_options,
    whisper_model_options_with_state,
)
from utils import WHISPER_MODELS, WHISPER_LANGUAGES, PERFORMANCE_MODES


def _fill_combo(combo: QComboBox, items: list) -> None:
    for item in items:
        if isinstance(item, tuple):
            combo.addItem(item[1], item[0])
        else:
            combo.addItem(item)


def _fill_model_combo(combo: QComboBox) -> None:
    """Model items get a "· downloaded"/"· will download" suffix (B10,
    docs/IMPROVEMENT_PLAN_2026-08.ru.md) — a text signal rather than
    color alone, and picked up by ElidingComboBox's own tooltip sync for
    free since it's baked into the item text itself."""
    for key, label, downloaded in whisper_model_options_with_state():
        combo.addItem(f"{label} {model_state_suffix(downloaded)}", key)


class TranscribeOptions(QFrame):
    """Panel with the transcription settings (model/language/translate/
    performance/diarization).

    Embedded directly wherever it's used (currently: borrowed by
    ui/recipe_editor.py's dialog for the duration of one edit) — it was
    once also a floating popover anchored under a header gear button, but
    that header (and the popover chrome for it) was retired along with
    the pre-redesign layout, and every remaining construction site embeds
    it. MainWindow keeps referring to its widgets under their historical
    attribute names (self.model_combo, etc.) by aliasing to this panel's
    widgets after construction.
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setProperty("role", "card")
        self._setup_ui()
        self._load_config()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Model
        model_row = QHBoxLayout()
        model_label = QLabel(tr("label_model"))
        model_label.setProperty("role", "muted")
        model_row.addWidget(model_label)
        self.model_combo = ElidingComboBox()
        self.model_combo.setMinimumWidth(200)
        _fill_model_combo(self.model_combo)
        self.model_combo.currentIndexChanged.connect(self.changed.emit)
        model_row.addWidget(self.model_combo, stretch=1)
        layout.addLayout(model_row)

        # Language + translate
        lang_row = QHBoxLayout()
        lang_label = QLabel(tr("label_language"))
        lang_label.setProperty("role", "muted")
        lang_row.addWidget(lang_label)
        self.language_combo = ElidingComboBox()
        self.language_combo.setMinimumWidth(140)
        _fill_combo(self.language_combo, whisper_language_options())
        self.language_combo.currentIndexChanged.connect(self.changed.emit)
        lang_row.addWidget(self.language_combo, stretch=1)
        layout.addLayout(lang_row)

        self.translate_checkbox = QCheckBox("→ EN")
        self.translate_checkbox.setToolTip(tr("tooltip_translate"))
        self.translate_checkbox.toggled.connect(self.changed.emit)
        layout.addWidget(self.translate_checkbox)

        # Performance
        perf_row = QHBoxLayout()
        perf_label = QLabel(tr("label_mode"))
        perf_label.setProperty("role", "muted")
        perf_row.addWidget(perf_label)
        self.perf_combo = ElidingComboBox(sync_tooltip=False)
        self.perf_combo.setMinimumWidth(160)
        _fill_combo(self.perf_combo, [(m[0], m[1]) for m in performance_mode_options()])
        self.perf_combo.setToolTip(tr("tooltip_performance_mode"))
        self.perf_combo.currentIndexChanged.connect(self.changed.emit)
        perf_row.addWidget(self.perf_combo, stretch=1)
        layout.addLayout(perf_row)

        # Diarization
        self.diarization_checkbox = QCheckBox(tr("label_speakers"))
        self.diarization_checkbox.setToolTip(tr("tooltip_diarization"))
        self.diarization_checkbox.toggled.connect(self.changed.emit)
        layout.addWidget(self.diarization_checkbox)

    def _load_config(self) -> None:
        cfg = get_config()
        model_idx = next(
            (i for i, (k, _) in enumerate(WHISPER_MODELS) if k == cfg.default_model), 6
        )
        self.model_combo.setCurrentIndex(model_idx)
        lang_idx = next(
            (i for i, (k, _) in enumerate(WHISPER_LANGUAGES) if k == cfg.default_language), 0
        )
        self.language_combo.setCurrentIndex(lang_idx)
        perf_idx = next(
            (i for i, (k, *_) in enumerate(PERFORMANCE_MODES) if k == cfg.performance_mode), 1
        )
        self.perf_combo.setCurrentIndex(perf_idx)
        self.diarization_checkbox.setChecked(cfg.diarization_enabled)
        # Connected after the initial seeding above, so restoring the saved
        # values doesn't immediately write them back out again.
        self.changed.connect(self._persist)

    def params(self) -> dict:
        """The widget's current selections as a Recipe.params dict (B4,
        docs/IMPROVEMENT_PLAN_2026-08.ru.md) — what the recipe editor
        captures into the recipe being saved, on top of (not instead of)
        this panel's existing auto-persist-to-Config side effect."""
        return {
            "model": self.model_combo.currentData(),
            "language": self.language_combo.currentData(),
            "translate": self.translate_checkbox.isChecked(),
            "performance_mode": self.perf_combo.currentData(),
            "diarization": self.diarization_checkbox.isChecked(),
        }

    def apply_params(self, params: dict) -> None:
        """Seed the widgets from a recipe's saved params, falling back to
        whatever is already selected (i.e. Config's defaults, per
        _load_config()) for any key the recipe doesn't override — called
        by the recipe editor before it's shown, not on every keystroke."""
        model = params.get("model")
        if model:
            idx = self.model_combo.findData(model)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        language = params.get("language")
        if language:
            idx = self.language_combo.findData(language)
            if idx >= 0:
                self.language_combo.setCurrentIndex(idx)
        if "translate" in params:
            self.translate_checkbox.setChecked(bool(params["translate"]))
        mode = params.get("performance_mode")
        if mode:
            idx = self.perf_combo.findData(mode)
            if idx >= 0:
                self.perf_combo.setCurrentIndex(idx)
        if "diarization" in params:
            self.diarization_checkbox.setChecked(bool(params["diarization"]))

    def refresh_model_state(self) -> None:
        """Re-check which models are downloaded and rebuild the model
        combo's item text (B10, docs/IMPROVEMENT_PLAN_2026-08.ru.md item
        3) — called after Settings is applied and before the recipe
        editor is (re)shown, never on every dropdown open. Preserves the
        current selection; blocked signals keep the rebuild from firing
        currentIndexChanged (and therefore _persist) as a side effect."""
        current = self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        _fill_model_combo(self.model_combo)
        idx = self.model_combo.findData(current)
        self.model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.model_combo.blockSignals(False)

    def _persist(self) -> None:
        """Write the current selection back to Config.

        Guarded: a combo is empty (and currentData() is None) only while
        the widget is being rebuilt, and Config.validate() rejects an
        unknown performance_mode outright — never overwrite a good saved
        value with a placeholder one.
        """
        cfg = get_config()
        model = self.model_combo.currentData()
        if model:
            cfg.default_model = model
        language = self.language_combo.currentData()
        if language:
            cfg.default_language = language
        mode = self.perf_combo.currentData()
        if mode:
            cfg.performance_mode = mode
        cfg.diarization_enabled = self.diarization_checkbox.isChecked()
        save_config()
