"""The single entry point for starting new work (see
docs/UI_REDESIGN_PLAN_2026-09.ru.md, B6): one task, one column — pick a
source, pick a recipe, launch. Replaces ui/draft_record.py, which this
absorbs (the source switcher below is that same structure) plus the
recipe picker the plan calls for.

live_view.options_panel (setup/preflight/diagnostics) stacks above
live_view itself (session controls/transcript) on the "live" source
page, giving both halves of what used to be two different columns
(right-column inspector vs. center draft) one shared column here. B7
finishes the job on LiveView's own side: dropping its nested page
header, since StartView's title above already covers it.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from config import get_config, save_config
from core.i18n import tr
from domain.recipe import BUILTIN_RECIPES, TRANSCRIPT_ONLY
from ui.animated_button import AnimatedButton
from ui.components import FlowLayout
from ui.option_labels import whisper_model_options


class StartView(QWidget):
    """Choose a source, choose a recipe, launch.

    ``file_selector``/``recorder``/``live``/``folder`` are the same four
    source widgets ui/draft_record.py used to own — built once by
    MainWindow and handed in here, not constructed by this view, so a
    single instance of each keeps working across every navigation back
    to this screen.
    """

    process_requested = pyqtSignal()
    source_changed = pyqtSignal(str)
    recipe_changed = pyqtSignal(str)  # a BUILTIN_RECIPES_BY_KEY key
    configure_recipe_requested = pyqtSignal()

    def __init__(
        self,
        file_selector: QWidget,
        recorder: QWidget,
        live: QWidget,
        live_options: QWidget,
        folder: QWidget,
        transcribe_options_summary_source: QWidget,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._source_keys = ("file", "recorder", "live", "folder")
        # Sources whose result is launched by the button below. "live" runs
        # its own session controls and "folder" hands off to the queue, so
        # both keep it hidden; a finished recording, on the other hand, is
        # just a file, and hiding Launch on the recorder page left it with
        # no way at all to transcribe what had just been recorded.
        self._launchable_sources = ("file", "recorder")
        self._transcribe_options = transcribe_options_summary_source
        self._recipe_key = get_config().last_recipe or TRANSCRIPT_ONLY.builtin_key

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(16)

        title = QLabel(tr("draft_title"))
        title.setProperty("role", "page-title")
        root.addWidget(title)
        subtitle = QLabel(tr("draft_subtitle"))
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        switcher = QHBoxLayout()
        self._source_group = QButtonGroup(self)
        self._source_group.setExclusive(True)
        self._source_buttons: dict[str, QPushButton] = {}
        for index, key in enumerate(self._source_keys):
            button = QPushButton(tr(f"draft_source_{key}"))
            button.setCheckable(True)
            button.setProperty("role", "quick-chip")
            button.clicked.connect(lambda _checked, i=index, name=key: self.set_source(name, i))
            self._source_group.addButton(button)
            self._source_buttons[key] = button
            switcher.addWidget(button)
        root.addLayout(switcher)

        self.stack = QStackedWidget()
        live_body = QWidget()
        live_layout = QVBoxLayout(live_body)
        live_layout.setContentsMargins(0, 0, 0, 0)
        live_layout.setSpacing(8)
        live_layout.addWidget(live_options)
        live_layout.addWidget(live, stretch=1)
        # Live is by far the tallest source page (session setup + preflight
        # + diagnostics stacked above the session controls and transcript).
        # Everything else on this screen — title, source switcher, recipe
        # chips, summary — takes its share first, which left the setup
        # panel about 114px for a layout whose own minimum is 274: a
        # QVBoxLayout given less than its minimum does not clip, it lets
        # its children overlap, and the device combo was drawn straight
        # over the "Microphone"/"Meeting audio" checkboxes. Scroll instead.
        live_page = QScrollArea()
        live_page.setWidgetResizable(True)
        live_page.setFrameShape(QFrame.Shape.NoFrame)
        live_page.setWidget(live_body)
        for widget in (file_selector, recorder, live_page, folder):
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.addWidget(widget)
            if widget is not live_page:
                page_layout.addStretch()
            self.stack.addWidget(page)
        root.addWidget(self.stack, stretch=1)

        self._recipe_label = QLabel(tr("start_recipe_label"))
        self._recipe_label.setProperty("role", "muted")
        root.addWidget(self._recipe_label)

        # A plain QHBoxLayout would squeeze these chips narrower than
        # their own label at 900px width — FlowLayout wraps to a second
        # row instead (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, A2, the
        # same fix Library's filter chips needed).
        self._recipe_row_widget = QWidget()
        recipe_row = FlowLayout(self._recipe_row_widget, spacing=6)
        self._recipe_group = QButtonGroup(self)
        self._recipe_group.setExclusive(True)
        self._recipe_buttons: dict[str, QPushButton] = {}
        for recipe in BUILTIN_RECIPES:
            button = QPushButton(tr(f"recipe_{recipe.builtin_key}"))
            button.setCheckable(True)
            button.setProperty("role", "quick-chip")
            button.setChecked(recipe.builtin_key == self._recipe_key)
            button.clicked.connect(
                lambda _checked, key=recipe.builtin_key: self._select_recipe(key)
            )
            self._recipe_group.addButton(button)
            self._recipe_buttons[recipe.builtin_key] = button
            recipe_row.addWidget(button)
        configure_btn = QPushButton(tr("start_recipe_configure"))
        configure_btn.setProperty("role", "quick-chip")
        configure_btn.clicked.connect(self.configure_recipe_requested.emit)
        recipe_row.addWidget(configure_btn)
        root.addWidget(self._recipe_row_widget)

        self.process_button = AnimatedButton(tr("start_launch"))
        self.process_button.setProperty("variant", "primary")
        self.process_button.setEnabled(False)
        self.process_button.setToolTip(tr("tooltip_process_disabled"))
        self.process_button.clicked.connect(self.process_requested.emit)
        root.addWidget(self.process_button)

        # A container widget (not a bare layout added via addLayout) so
        # set_source() can hide the whole row for "folder" — see below.
        self._summary_row_widget = QWidget()
        summary_row = QHBoxLayout(self._summary_row_widget)
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(6)
        self._summary_label = QLabel("")
        self._summary_label.setProperty("role", "muted")
        summary_row.addWidget(self._summary_label, stretch=1)
        change_link = QPushButton(tr("start_recipe_change"))
        change_link.setProperty("variant", "ghost")
        change_link.clicked.connect(self.configure_recipe_requested.emit)
        summary_row.addWidget(change_link)
        root.addWidget(self._summary_row_widget)

        self.set_source("file", 0)
        self.refresh_summary()

    # ── source ───────────────────────────────────────────────────────

    def set_source(self, key: str, index: int | None = None) -> None:
        if key not in self._source_keys:
            return
        index = self._source_keys.index(key) if index is None else index
        self._source_buttons[key].setChecked(True)
        self.stack.setCurrentIndex(index)
        self.process_button.setVisible(key in self._launchable_sources)
        # The folder queue only transcribes each item (see
        # MainWindow._on_batch_item_finished) — it does not run a recipe
        # against them yet (docs/IMPROVEMENT_PLAN_2026-08.ru.md, A6). The
        # recipe picker promised otherwise by being visible here, so it's
        # hidden for this source rather than offering a choice queued
        # files don't actually honour.
        recipe_ui_applies = key != "folder"
        self._recipe_label.setVisible(recipe_ui_applies)
        self._recipe_row_widget.setVisible(recipe_ui_applies)
        self._summary_row_widget.setVisible(recipe_ui_applies)
        self.source_changed.emit(key)

    def set_process_enabled(self, enabled: bool) -> None:
        self.process_button.setEnabled(enabled)
        self.process_button.setToolTip(
            tr("tooltip_process") if enabled else tr("tooltip_process_disabled")
        )

    def current_source(self) -> str:
        return self._source_keys[self.stack.currentIndex()]

    # ── recipe ───────────────────────────────────────────────────────

    def current_recipe_key(self) -> str:
        return self._recipe_key

    def set_recipe(self, key: str) -> None:
        """Select *key* programmatically (e.g. after the recipe editor
        saves a change) without re-persisting it — _select_recipe() does
        that when the change originates from a button click.

        *key* may be "custom" (the recipe editor's one saved custom slot,
        see ui/recipe_editor.py) — it has no matching chip, so every chip
        is simply left unchecked rather than the call being ignored.
        """
        self._recipe_key = key
        button = self._recipe_buttons.get(key)
        if button is not None:
            button.setChecked(True)
        else:
            checked = self._recipe_group.checkedButton()
            if checked is not None:
                self._recipe_group.setExclusive(False)
                checked.setChecked(False)
                self._recipe_group.setExclusive(True)
        self.refresh_summary()

    def _select_recipe(self, key: str) -> None:
        self.set_recipe(key)
        cfg = get_config()
        cfg.last_recipe = key
        save_config()
        self.recipe_changed.emit(key)

    def select_recipe(self, key: str) -> None:
        """Pick recipe *key* as if its chip were clicked: checks the
        chip, persists it as Config.last_recipe and emits recipe_changed
        — the command palette's "Run: <recipe>" (B8) uses this same path
        rather than duplicating _select_recipe's persistence."""
        self._select_recipe(key)

    def refresh_summary(self) -> None:
        """Re-read the transcription options widget's current selections
        into the muted summary line under the launch button — called after
        recipe_editor.py's dialog closes, since that's where those combos
        actually live now.

        Model text is looked up from whisper_model_options() rather than
        read as model_combo.currentText() directly: since B10 that combo's
        item text carries a "· downloaded"/"· will download" suffix
        (docs/IMPROVEMENT_PLAN_2026-08.ru.md) meant for the combo itself,
        not for this already-long one-line summary — appending it here
        overflowed the label (a plain QLabel with no eliding or wrapping)
        at the gallery's smallest tested width.
        """
        opts = self._transcribe_options
        model = ""
        if hasattr(opts, "model_combo"):
            key = opts.model_combo.currentData()
            model = next(
                (label for k, label in whisper_model_options() if k == key),
                opts.model_combo.currentText(),
            )
        language = opts.language_combo.currentText() if hasattr(opts, "language_combo") else ""
        mode = opts.perf_combo.currentText() if hasattr(opts, "perf_combo") else ""
        self._summary_label.setText(
            tr("start_recipe_summary", model=model, language=language, mode=mode)
        )
