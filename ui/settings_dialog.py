"""
Whispered - Settings Dialog
Unified settings window (Ctrl+,).
"""

import os
import subprocess
import sys
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QStackedWidget, QWidget,
    QPushButton, QLabel, QComboBox, QCheckBox, QLineEdit,
    QDialogButtonBox, QSpinBox, QFormLayout,
    QDoubleSpinBox, QMessageBox, QPlainTextEdit
)
from PyQt6.QtCore import pyqtSignal

from config import get_config, save_config
from utils import get_models_dir
from ui.option_labels import (
    performance_mode_options,
    whisper_language_options,
    whisper_model_options,
)
from ui.theme import apply_theme, set_role
from core.lm_status_worker import LMStatusWorker
from core.worker_registry import WorkerRegistry
from core.logger import get_logger
from core.i18n import tr

logger = get_logger(__name__)


class SettingsDialog(QDialog):
    """Unified settings dialog (Ctrl+,)."""

    settings_applied = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = get_config()
        self._checker: Optional[LMStatusWorker] = None
        self._registry = WorkerRegistry(parent=self)
        self._pending_theme: Optional[str] = None
        self._lang_changed: bool = False
        self._setup_ui()
        self._load_values()
        self._connect_dirty_signals()
        self._apply_button.setEnabled(False)
        # Qt otherwise hands initial keyboard focus to _categories (the
        # first focusable widget in tab order) — its QSS focus ring then
        # stays lit for as long as the dialog is open, reading as a
        # permanently "stuck" selection outline rather than a transient
        # keyboard-focus indicator. OK is the conventional initial-focus
        # target for a dialog anyway.
        self._ok_button.setFocus()

    # ------------------------------------------------------------------ setup

    def _setup_ui(self):
        self.setWindowTitle(tr("settings_title"))
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        content = QHBoxLayout()
        content.setSpacing(16)

        sidebar = QVBoxLayout()
        sidebar.setContentsMargins(0, 0, 0, 0)
        sidebar.setSpacing(8)
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(tr("settings_search_placeholder"))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._filter_categories)
        self._search_edit.setFixedWidth(190)
        sidebar.addWidget(self._search_edit)
        self._categories = QListWidget()
        self._categories.setProperty("role", "sidebar")
        self._categories.setFixedWidth(190)
        self._categories.setAccessibleName(tr("settings_title"))
        sidebar.addWidget(self._categories, stretch=1)

        self._pages = QStackedWidget()
        # General has far fewer controls than its siblings; the stack
        # sizes every page to the tallest one, so wrapping its form in its
        # own compact card (instead of letting the page itself be the
        # full-height card) keeps the "boxed" area hugging its content
        # instead of stretching into ~400px of bordered empty space.
        pages = (
            ("settings_category_general", self._build_general_tab()),
            ("settings_category_transcription", self._build_transcription_tab()),
            ("settings_category_recording_live", self._build_recording_live_tab()),
            ("settings_category_diarization", self._build_diarization_tab()),
            ("settings_category_ai", self._build_ai_tab()),
            ("settings_category_covers", self._build_covers_tab()),
        )
        self._category_search_text: list[str] = []
        for label_key, page in pages:
            self._categories.addItem(tr(label_key))
            self._pages.addWidget(page)
            # Indexes every child with a plain .text() — QLabel/QCheckBox/
            # QPushButton, which also covers QFormLayout's own auto-built
            # row-label QLabels — so a search for a control's own label
            # (e.g. "Диаризация" typed while on another tab, or "HF
            # token") finds the category that has it, not just a search
            # matching the category name itself.
            words = [tr(label_key)]
            for widget in page.findChildren(QWidget):
                text_fn = getattr(widget, "text", None)
                if callable(text_fn):
                    value = text_fn()
                    if value:
                        words.append(value)
            self._category_search_text.append(" ".join(words).casefold())
        self._categories.currentRowChanged.connect(self._pages.setCurrentIndex)
        self._categories.setCurrentRow(0)
        content.addLayout(sidebar)
        content.addWidget(self._pages, stretch=1)
        root.addLayout(content, stretch=1)

        # Button box
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        btn_box.setContentsMargins(0, 0, 0, 0)
        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)
        self._ok_button = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        self._ok_button.setText(tr("settings_ok"))
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("settings_cancel"))
        self._apply_button = btn_box.button(QDialogButtonBox.StandardButton.Apply)
        self._apply_button.setText(tr("settings_apply"))
        self._apply_button.clicked.connect(self._on_apply)
        root.addWidget(btn_box)

        # The stack sizes every page to its tallest (Транскрипция/Обложки,
        # ~320px), so a dialog hardcoded to 840x680 left ~280px of empty
        # space below EVERY tab's content, not just General's (see
        # docs/UI_REDESIGN_PLAN_2026-09.ru.md, A7). Size to what the
        # content actually needs instead of a fixed guess; the floor stays
        # comfortably below that so a user can still shrink it a bit.
        hint = self.sizeHint()
        self.setMinimumSize(max(620, hint.width() - 120), max(360, hint.height() - 40))
        self.resize(hint)

    def _filter_categories(self, query: str) -> None:
        needle = query.strip().casefold()
        first_visible = None
        for row in range(self._categories.count()):
            item = self._categories.item(row)
            visible = not needle or needle in self._category_search_text[row]
            item.setHidden(not visible)
            if visible and first_visible is None:
                first_visible = row
        if needle and first_visible is not None:
            current = self._categories.currentRow()
            if current < 0 or self._categories.item(current).isHidden():
                self._categories.setCurrentRow(first_visible)

    # ------------------------------------------------------------------ tabs

    def _build_general_tab(self) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)

        card = QWidget()
        card.setProperty("role", "form-section")
        layout = QFormLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Theme
        self._theme_combo = QComboBox()
        self._theme_combo.addItem(tr("settings_theme_dark"), "dark")
        self._theme_combo.addItem(tr("settings_theme_light"), "light")
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        layout.addRow(tr("settings_theme"), self._theme_combo)

        # History
        self._history_chk = QCheckBox(tr("settings_history_enabled"))
        layout.addRow(self._history_chk)

        clear_btn = QPushButton(tr("settings_clear_history"))
        clear_btn.clicked.connect(self._clear_history)
        layout.addRow(clear_btn)

        # UI language
        self._lang_ui_combo = QComboBox()
        self._lang_ui_combo.addItem("Auto", "auto")
        self._lang_ui_combo.addItem("English", "en")
        self._lang_ui_combo.addItem("Русский", "ru")
        layout.addRow(tr("settings_ui_language"), self._lang_ui_combo)

        tab_layout.addWidget(card)
        tab_layout.addStretch()
        return tab

    def _build_transcription_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Default model
        self._model_combo = QComboBox()
        for key, label in whisper_model_options():
            self._model_combo.addItem(label, key)
        layout.addRow(tr("settings_default_model"), self._model_combo)

        # Default language
        self._lang_combo = QComboBox()
        for key, label in whisper_language_options():
            self._lang_combo.addItem(label, key)
        layout.addRow(tr("settings_default_language"), self._lang_combo)

        # Performance mode
        self._perf_combo = QComboBox()
        for key, label, *_ in performance_mode_options():
            self._perf_combo.addItem(label, key)
        layout.addRow(tr("settings_performance"), self._perf_combo)

        # Models directory (read-only)
        models_row = QHBoxLayout()
        self._models_dir_label = QLabel()
        self._models_dir_label.setProperty("role", "muted")
        self._models_dir_label.setProperty("size", "small")
        self._models_dir_label.setText(get_models_dir())
        models_row.addWidget(self._models_dir_label, stretch=1)

        open_btn = QPushButton(tr("btn_open_folder"))
        open_btn.clicked.connect(self._open_models_dir)
        models_row.addWidget(open_btn)

        models_container = QWidget()
        models_container.setLayout(models_row)
        layout.addRow(tr("settings_models_dir"), models_container)

        # Custom vocabulary
        self._vocab_edit = QPlainTextEdit()
        self._vocab_edit.setPlaceholderText(tr("settings_vocab_hint"))
        self._vocab_edit.setMaximumHeight(90)
        layout.addRow(tr("settings_vocab"), self._vocab_edit)

        return tab

    def _build_recording_live_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self._mic_combo = QComboBox()
        self._populate_mic_devices()
        layout.addRow(tr("settings_mic_device"), self._mic_combo)

        self._live_chk = QCheckBox(tr("settings_live_enabled"))
        layout.addRow(self._live_chk)

        hint = QLabel(tr("settings_live_hint"))
        hint.setProperty("role", "muted")
        hint.setWordWrap(True)
        layout.addRow(hint)
        return tab

    def _build_covers_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self._cover_layout_combo = QComboBox()
        for label, value in (
            (tr("cover_layout_duo"), "duo"),
            (tr("cover_layout_solo"), "solo"),
            (tr("cover_layout_text"), "text_only"),
        ):
            self._cover_layout_combo.addItem(label, value)
        self._cover_variant_combo = QComboBox()
        self._cover_variant_combo.addItem(tr("cover_variant_mint"), "mint")
        self._cover_variant_combo.addItem(tr("cover_variant_warm"), "warm")
        self._cover_host_name_edit = QLineEdit()
        self._cover_host_photo_edit = QLineEdit()
        self._cover_provider_combo = QComboBox()
        self._cover_provider_combo.addItem("Local", "local")
        self._cover_provider_combo.addItem("ComfyUI", "comfyui")
        self._cover_provider_combo.addItem("HTTP", "http")
        self._cover_comfy_edit = QLineEdit()
        self._cover_upscale_chk = QCheckBox(tr("settings_cover_upscale"))
        self._cover_shorts_chk = QCheckBox(tr("settings_cover_shorts"))
        layout.addRow(tr("cover_layout"), self._cover_layout_combo)
        layout.addRow(tr("cover_variant"), self._cover_variant_combo)
        layout.addRow(tr("settings_cover_host_name"), self._cover_host_name_edit)
        layout.addRow(tr("settings_cover_host_photo"), self._cover_host_photo_edit)
        layout.addRow(tr("settings_cover_provider"), self._cover_provider_combo)
        layout.addRow("ComfyUI URL", self._cover_comfy_edit)
        layout.addRow(self._cover_upscale_chk)
        layout.addRow(self._cover_shorts_chk)
        return tab

    def _build_diarization_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self._diarization_chk = QCheckBox(tr("settings_diarization_enable"))
        layout.addRow(self._diarization_chk)

        # HF token
        token_row = QHBoxLayout()
        self._hf_token_edit = QLineEdit()
        self._hf_token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._hf_token_edit.setPlaceholderText("hf_…")
        token_row.addWidget(self._hf_token_edit, stretch=1)

        show_btn = QPushButton(tr("settings_show_secret"))
        show_btn.setCheckable(True)
        show_btn.toggled.connect(
            lambda checked: self._hf_token_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
            )
        )
        token_row.addWidget(show_btn)

        token_container = QWidget()
        token_container.setLayout(token_row)
        layout.addRow(tr("settings_hf_token"), token_container)

        # Number of speakers
        self._speakers_spin = QSpinBox()
        self._speakers_spin.setSpecialValueText(tr("speakers_auto"))
        self._speakers_spin.setRange(0, 8)   # 0 = auto
        self._speakers_spin.setValue(0)
        layout.addRow(tr("settings_num_speakers"), self._speakers_spin)

        note = QLabel(tr("diarization_note"))
        note.setProperty("role", "muted")
        note.setProperty("size", "small")
        note.setWordWrap(True)
        layout.addRow(note)

        return tab

    def _build_ai_tab(self) -> QWidget:
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Posts / AI panel URL
        self._lm_url_edit = QLineEdit()
        self._lm_url_edit.setPlaceholderText("http://localhost:1234/v1")
        layout.addRow(tr("settings_lm_url"), self._lm_url_edit)

        check_row = QHBoxLayout()
        self._check_btn = QPushButton(tr("btn_test_connection"))
        self._check_btn.setProperty("variant", "primary")
        self._check_btn.clicked.connect(self._check_connection)
        check_row.addWidget(self._check_btn)
        self._check_result = QLabel("")
        self._check_result.setProperty("size", "small")
        check_row.addWidget(self._check_result, stretch=1)
        check_container = QWidget()
        check_container.setLayout(check_row)
        layout.addRow(check_container)

        # Book pipeline URL
        self._book_lm_url_edit = QLineEdit()
        self._book_lm_url_edit.setPlaceholderText("http://localhost:1234/v1")
        layout.addRow(tr("settings_book_lm_url"), self._book_lm_url_edit)

        # Book model name
        self._book_model_edit = QLineEdit()
        self._book_model_edit.setPlaceholderText(tr("settings_book_model_placeholder"))
        layout.addRow(tr("settings_book_model"), self._book_model_edit)

        # Book temperature
        self._book_temp_spin = QDoubleSpinBox()
        self._book_temp_spin.setRange(0.0, 2.0)
        self._book_temp_spin.setSingleStep(0.1)
        self._book_temp_spin.setDecimals(1)
        layout.addRow(tr("settings_book_temp"), self._book_temp_spin)

        return tab

    # ------------------------------------------------------------------ helpers

    def _load_values(self):
        """Populate widgets from current config."""
        cfg = self._cfg

        # General
        idx = self._theme_combo.findData(cfg.theme)
        self._theme_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._history_chk.setChecked(cfg.history_enabled)
        self._live_chk.setChecked(getattr(cfg, "live_transcription_enabled", False))
        idx = self._lang_ui_combo.findData(getattr(cfg, "ui_language", "auto"))
        self._lang_ui_combo.setCurrentIndex(idx if idx >= 0 else 0)

        # Transcription
        idx = self._model_combo.findData(getattr(cfg, "default_model", "large-v3-turbo"))
        self._model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self._lang_combo.findData(getattr(cfg, "default_language", "auto"))
        self._lang_combo.setCurrentIndex(idx if idx >= 0 else 0)
        idx = self._perf_combo.findData(getattr(cfg, "performance_mode", "balanced"))
        self._perf_combo.setCurrentIndex(idx if idx >= 0 else 1)
        vocab = getattr(cfg, "custom_vocabulary", []) or []
        self._vocab_edit.setPlainText("\n".join(vocab))
        saved_mic = getattr(cfg, "mic_device_index", None)
        idx = self._mic_combo.findData(saved_mic)
        self._mic_combo.setCurrentIndex(idx if idx >= 0 else 0)

        # Diarization
        self._diarization_chk.setChecked(cfg.diarization_enabled)
        self._hf_token_edit.setText(cfg.hf_token or "")
        self._speakers_spin.setValue(cfg.default_num_speakers or 0)

        # AI
        self._lm_url_edit.setText(cfg.lm_studio_url)
        self._book_lm_url_edit.setText(cfg.book_lm_url)
        self._book_model_edit.setText(cfg.book_model_name or "")
        self._book_temp_spin.setValue(cfg.book_temperature)

        for combo, value in (
            (self._cover_layout_combo, cfg.cover_layout),
            (self._cover_variant_combo, cfg.cover_variant),
            (self._cover_provider_combo, cfg.cover_image_provider),
        ):
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)
        self._cover_host_name_edit.setText(cfg.cover_host_name)
        self._cover_host_photo_edit.setText(cfg.cover_host_photo)
        self._cover_comfy_edit.setText(cfg.cover_comfy_url)
        self._cover_upscale_chk.setChecked(cfg.cover_upscale_enabled)
        self._cover_shorts_chk.setChecked(cfg.cover_export_shorts)

    def _save_values(self):
        """Write widget values back to config."""
        cfg = self._cfg
        prev_lang = getattr(cfg, "ui_language", "auto")

        # General
        cfg.theme = self._theme_combo.currentData() or "dark"
        cfg.history_enabled = self._history_chk.isChecked()
        cfg.live_transcription_enabled = self._live_chk.isChecked()
        cfg.ui_language = self._lang_ui_combo.currentData() or "auto"

        # Transcription
        cfg.default_model = self._model_combo.currentData() or "large-v3-turbo"
        cfg.default_language = self._lang_combo.currentData() or "auto"
        cfg.performance_mode = self._perf_combo.currentData() or "balanced"
        raw_vocab = self._vocab_edit.toPlainText()
        cfg.custom_vocabulary = [t.strip() for t in raw_vocab.splitlines() if t.strip()]
        cfg.mic_device_index = self._mic_combo.currentData()

        # Diarization
        cfg.diarization_enabled = self._diarization_chk.isChecked()
        cfg.hf_token = self._hf_token_edit.text().strip() or None
        speakers = self._speakers_spin.value()
        cfg.default_num_speakers = speakers if speakers > 0 else None

        # AI
        cfg.lm_studio_url = self._lm_url_edit.text().strip() or "http://localhost:1234/v1"
        cfg.book_lm_url = self._book_lm_url_edit.text().strip() or "http://localhost:1234/v1"
        cfg.book_model_name = self._book_model_edit.text().strip()
        cfg.book_temperature = self._book_temp_spin.value()

        cfg.cover_layout = self._cover_layout_combo.currentData() or "duo"
        cfg.cover_variant = self._cover_variant_combo.currentData() or "mint"
        cfg.cover_host_name = self._cover_host_name_edit.text().strip()
        cfg.cover_host_photo = self._cover_host_photo_edit.text().strip()
        cfg.cover_image_provider = self._cover_provider_combo.currentData() or "local"
        cfg.cover_comfy_url = self._cover_comfy_edit.text().strip() or "http://127.0.0.1:8188"
        cfg.cover_upscale_enabled = self._cover_upscale_chk.isChecked()
        cfg.cover_export_shorts = self._cover_shorts_chk.isChecked()

        save_config()

        if cfg.ui_language != prev_lang:
            self._lang_changed = True

    def _connect_dirty_signals(self) -> None:
        for widget in self.findChildren(QComboBox):
            widget.currentIndexChanged.connect(self._mark_dirty)
        for widget in self.findChildren(QCheckBox):
            widget.toggled.connect(self._mark_dirty)
        # _search_edit filters the category list; it holds no setting of
        # its own and must not enable Apply just because the user searched.
        for widget in self.findChildren(QLineEdit):
            if widget is self._search_edit:
                continue
            widget.textChanged.connect(self._mark_dirty)
        for widget in self.findChildren(QPlainTextEdit):
            widget.textChanged.connect(self._mark_dirty)
        for widget in self.findChildren(QSpinBox):
            widget.valueChanged.connect(self._mark_dirty)
        for widget in self.findChildren(QDoubleSpinBox):
            widget.valueChanged.connect(self._mark_dirty)

    def _mark_dirty(self, *_args) -> None:
        self._apply_button.setEnabled(True)

    def _on_theme_changed(self, _index: int):
        """Apply theme immediately for preview."""
        from PyQt6.QtWidgets import QApplication
        name = self._theme_combo.currentData() or "dark"
        app = QApplication.instance()
        if app:
            apply_theme(app, name)
        self._pending_theme = name

    def _on_apply(self):
        self._lang_changed = False
        self._save_values()
        self._apply_button.setEnabled(False)
        self.settings_applied.emit()
        if self._lang_changed:
            QMessageBox.information(
                self,
                tr("app_title"),
                tr("settings_language_restart"),
            )

    def _on_ok(self):
        self._stop_checker()
        self._lang_changed = False
        self._save_values()
        self.settings_applied.emit()
        if self._lang_changed:
            QMessageBox.information(
                self,
                tr("app_title"),
                tr("settings_language_restart"),
            )
        self.accept()

    def _stop_checker(self) -> None:
        """Retire any in-flight connection check through ``WorkerRegistry``.

        Retirement disconnects ``status_ready`` and calls ``cancel()``
        immediately but keeps a strong reference to the QThread until it
        actually finishes, then ``deleteLater()``s it. That's the part a
        bare ``self._checker.wait(1000); self._checker = None`` got wrong:
        the network probe's socket timeout can outlast a 1 s wait, and
        dropping the last reference to a still-running QThread — or later
        letting the dialog itself be destroyed while it's still alive — is
        what Qt aborts the process for.
        """
        if self._checker is not None:
            self._registry.retire(self._checker)
            self._checker = None

    def reject(self):
        """Cancel: revert theme preview and stop any running connection check."""
        self._stop_checker()

        if self._pending_theme is not None:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                apply_theme(app, self._cfg.theme)
        super().reject()

    def _open_models_dir(self):
        path = get_models_dir()
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _check_connection(self):
        if self._checker and self._checker.isRunning():
            return
        url = self._lm_url_edit.text().strip() or "http://localhost:1234/v1"
        self._check_result.setText(tr("connection_checking"))
        set_role(self._check_result, "muted")
        self._check_result.setProperty("size", "small")
        self._check_btn.setEnabled(False)

        self._checker = LMStatusWorker(url, parent=self)
        self._checker.status_ready.connect(self._on_check_result)
        self._registry.register(self._checker, name="lm_status_checker")
        self._checker.start()

    def _on_check_result(self, connected: bool, detail: str):
        self._check_btn.setEnabled(True)
        if connected:
            # detail is empty when the server answers with no model loaded.
            detail = detail or tr("connection_ok_no_model")
            self._check_result.setText(tr("connection_ok", detail=detail))
            set_role(self._check_result, "success-text")
        else:
            self._check_result.setText(tr("connection_fail", detail=detail[:60]))
            set_role(self._check_result, "danger-text")
        self._check_result.setProperty("size", "small")

    def _populate_mic_devices(self):
        """Fill the mic device combo with available input devices."""
        self._mic_combo.clear()
        self._mic_combo.addItem(tr("settings_mic_default"), None)
        try:
            from core.recorder import list_input_devices
            for dev in list_input_devices():
                self._mic_combo.addItem(dev["name"], dev["index"])
        except Exception:
            pass

    def _clear_history(self):
        reply = QMessageBox.question(
            self,
            tr("history_clear_title"),
            tr("history_clear_msg"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            from core.history import get_history_store
            n = get_history_store().clear()
            logger.info("History cleared from settings: %d records", n)
        except Exception as e:
            logger.warning("History clear failed: %s", e)
