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

from application.readiness import (
    CHECKING, MISSING, OK, WARN, ReadinessFacts, recipe_checks,
)
from config import get_config, save_config
from core.i18n import tr
from ui.i18n_helpers import Retranslator
from domain.recipe import BUILTIN_RECIPES, Recipe, TRANSCRIPT_ONLY
from ui.animated_button import AnimatedButton
from ui.components import FlowLayout
from ui.option_labels import (
    recipe_label, whisper_model_options, whisper_model_options_with_state,
)

_CHECK_GLYPHS = {OK: "✓", WARN: "◌", MISSING: "✕", CHECKING: "…"}
_CHECK_ROLES = {OK: "success-text", WARN: "warning-text", MISSING: "danger-text", CHECKING: "dim"}


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
    # A readiness check's fix was clicked: application.readiness FIX_* key.
    fix_requested = pyqtSignal(str)

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
        self._recipes: dict[str, Recipe] = {}
        # Facts the readiness line needs that are slow or external:
        # LM Studio's state comes from the status bar's own probe
        # (set_llm_status); ffmpeg and the speaker model are looked up
        # once per session.
        self._llm_reachable: bool | None = None
        self._ffmpeg_found: bool | None = None
        self._diarize_installed: bool | None = None
        self._i18n = Retranslator()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(16)

        title = self._i18n.text(QLabel(), "draft_title")
        title.setProperty("role", "page-title")
        root.addWidget(title)
        subtitle = self._i18n.text(QLabel(), "draft_subtitle")
        subtitle.setProperty("role", "muted")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        switcher = QHBoxLayout()
        self._source_group = QButtonGroup(self)
        self._source_group.setExclusive(True)
        self._source_buttons: dict[str, QPushButton] = {}
        for index, key in enumerate(self._source_keys):
            button = self._i18n.text(QPushButton(), f"draft_source_{key}")
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
            self.stack.addWidget(page)
        # Only the Live page grows to fill the screen; for the others the
        # recipe follows right under the source instead of half a screen
        # further down (see set_source()).
        root.addWidget(self.stack)
        self._root = root

        recipe_card = QWidget()
        recipe_card.setProperty("role", "form-section")
        recipe_layout = QVBoxLayout(recipe_card)
        recipe_layout.setContentsMargins(16, 12, 16, 14)
        recipe_layout.setSpacing(8)
        self._recipe_card = recipe_card

        self._recipe_label = self._i18n.text(QLabel(), "start_recipe_label")
        self._recipe_label.setProperty("role", "section-title")
        recipe_layout.addWidget(self._recipe_label)

        # A plain QHBoxLayout would squeeze these chips narrower than
        # their own label at 900px width — FlowLayout wraps to a second
        # row instead (see docs/UI_REDESIGN_PLAN_2026-09.ru.md, A2, the
        # same fix Library's filter chips needed).
        self._recipe_row_widget = QWidget()
        self._recipe_row_layout = FlowLayout(self._recipe_row_widget, spacing=6)
        self._recipe_group = QButtonGroup(self)
        self._recipe_group.setExclusive(True)
        self._recipe_buttons: dict[str, QPushButton] = {}
        # A custom recipe (B4, docs/IMPROVEMENT_PLAN_2026-08.ru.md) is
        # named, unlike a built-in's stable key, so its chip needs a
        # button that survives being torn down and rebuilt whenever
        # Config.recipes changes — see _build_recipe_chips().
        self._configure_btn = self._i18n.text(QPushButton(), "start_recipe_configure")
        self._configure_btn.setProperty("role", "quick-chip")
        self._configure_btn.clicked.connect(self.configure_recipe_requested.emit)
        self._build_recipe_chips()
        recipe_layout.addWidget(self._recipe_row_widget)

        # What the selected recipe will do, step by step.
        self._steps_label = QLabel()
        self._steps_label.setProperty("role", "muted")
        self._steps_label.setWordWrap(True)
        recipe_layout.addWidget(self._steps_label)

        # Whether it can run right now (S1): one glyph + text per check,
        # with a link to the fix where there is one.
        self._ready_widget = QWidget()
        self._ready_widget.setProperty("role", "transparent")
        self._recipe_row_widget.setProperty("role", "transparent")
        self._ready_layout = FlowLayout(self._ready_widget, spacing=10)
        self._ready_layout.setContentsMargins(0, 0, 0, 0)
        recipe_layout.addWidget(self._ready_widget)
        root.addWidget(recipe_card)

        launch_row = QHBoxLayout()
        launch_row.setSpacing(12)
        self.process_button = self._i18n.text(AnimatedButton(), "start_launch")
        self.process_button.setProperty("variant", "primary")
        self.process_button.setMinimumWidth(180)
        self.process_button.setEnabled(False)
        self.process_button.setToolTip(tr("tooltip_process_disabled"))
        self.process_button.clicked.connect(self.process_requested.emit)
        launch_row.addWidget(self.process_button)
        # Says why Launch is greyed out, instead of only in a tooltip.
        self._launch_hint = QLabel()
        self._launch_hint.setProperty("role", "muted")
        self._launch_hint.setWordWrap(True)
        launch_row.addWidget(self._launch_hint, stretch=1)
        root.addLayout(launch_row)

        # A container widget (not a bare layout added via addLayout) so
        # set_source() can hide the whole row for "folder" — see below.
        self._summary_row_widget = QWidget()
        summary_row = QHBoxLayout(self._summary_row_widget)
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.setSpacing(6)
        self._summary_label = QLabel("")
        self._summary_label.setProperty("role", "muted")
        summary_row.addWidget(self._summary_label, stretch=1)
        change_link = self._i18n.text(QPushButton(), "start_recipe_change")
        change_link.setProperty("variant", "ghost")
        change_link.clicked.connect(self.configure_recipe_requested.emit)
        summary_row.addWidget(change_link)
        root.addWidget(self._summary_row_widget)
        root.addStretch(1)

        self.set_source("file", 0)
        self.refresh_summary()
        self._i18n.call(self._retranslate_start)
        self._i18n.bind()

    def _retranslate_start(self) -> None:
        self.set_process_enabled(self.process_button.isEnabled())
        self._build_recipe_chips()
        self.refresh_summary()

    # ── source ───────────────────────────────────────────────────────

    def set_source(self, key: str, index: int | None = None) -> None:
        if key not in self._source_keys:
            return
        index = self._source_keys.index(key) if index is None else index
        self._source_buttons[key].setChecked(True)
        self.stack.setCurrentIndex(index)
        live = key == "live"
        self._root.setStretchFactor(self.stack, 1 if live else 0)
        self._root.setStretch(self._root.count() - 1, 0 if live else 1)
        self.process_button.setVisible(key in self._launchable_sources)
        self._launch_hint.setVisible(
            key in self._launchable_sources and not self.process_button.isEnabled()
        )
        # The folder queue only transcribes each item (see
        # MainWindow._on_batch_item_finished) — it does not run a recipe
        # against them yet (docs/IMPROVEMENT_PLAN_2026-08.ru.md, A6). The
        # recipe picker promised otherwise by being visible here, so it's
        # hidden for this source rather than offering a choice queued
        # files don't actually honour.
        recipe_ui_applies = key != "folder"
        self._recipe_card.setVisible(recipe_ui_applies)
        self._summary_row_widget.setVisible(recipe_ui_applies)
        self._render_launch_hint()
        self.source_changed.emit(key)

    def set_process_enabled(self, enabled: bool) -> None:
        self.process_button.setEnabled(enabled)
        self.process_button.setToolTip(
            tr("tooltip_process") if enabled else tr("tooltip_process_disabled")
        )
        self._render_launch_hint()

    def _render_launch_hint(self) -> None:
        key = self.current_source() if hasattr(self, "stack") else "file"
        enabled = self.process_button.isEnabled()
        self._launch_hint.setVisible(key in self._launchable_sources and not enabled)
        self._launch_hint.setText(
            tr("start_hint_record_first") if key == "recorder" else tr("start_hint_pick_file")
        )

    def current_source(self) -> str:
        return self._source_keys[self.stack.currentIndex()]

    # ── recipe ───────────────────────────────────────────────────────

    def current_recipe_key(self) -> str:
        return self._recipe_key

    def _build_recipe_chips(self) -> None:
        """(Re)build every recipe chip — the five built-ins plus one per
        Config.recipes entry (B4, docs/IMPROVEMENT_PLAN_2026-08.ru.md).
        Called from __init__ and again via refresh_recipe_chips()
        whenever the recipe editor adds, renames, or removes a custom
        recipe. self._configure_btn survives the rebuild (it carries no
        per-recipe state) — everything else is torn down and rebuilt."""
        while self._recipe_row_layout.count():
            item = self._recipe_row_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._configure_btn:
                self._recipe_group.removeButton(widget)
                widget.deleteLater()
        self._recipe_buttons = {}
        recipes = list(BUILTIN_RECIPES) + [
            Recipe.from_dict(entry) for entry in get_config().recipes
        ]
        self._recipes = {}
        for recipe in recipes:
            key = recipe.builtin_key or recipe.name
            self._recipes[key] = recipe
            button = QPushButton(recipe_label(recipe))
            button.setCheckable(True)
            button.setProperty("role", "quick-chip")
            button.setChecked(key == self._recipe_key)
            button.clicked.connect(lambda _checked, key=key: self._select_recipe(key))
            self._recipe_group.addButton(button)
            self._recipe_buttons[key] = button
            self._recipe_row_layout.addWidget(button)
        self._recipe_row_layout.addWidget(self._configure_btn)

    def refresh_recipe_chips(self) -> None:
        """Public entry point for after Config.recipes changes (save,
        save-as-new, delete in the recipe editor) — rebuild the chip row
        and re-select whatever Config.last_recipe now is."""
        self._recipe_key = get_config().last_recipe or TRANSCRIPT_ONLY.builtin_key
        self._build_recipe_chips()

    def set_recipe(self, key: str) -> None:
        """Select *key* programmatically (e.g. after the recipe editor
        saves a change) without re-persisting it — _select_recipe() does
        that when the change originates from a button click. Every
        recipe (built-in or custom) has a chip since B4, so there's no
        "no matching chip" case to fall back on any more."""
        self._recipe_key = key
        button = self._recipe_buttons.get(key)
        if button is not None:
            button.setChecked(True)
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
        self.refresh_readiness()

    # ── readiness (S1) ───────────────────────────────────────────────

    def set_llm_status(self, connected: bool, _detail: str = "") -> None:
        """The status bar's LM Studio probe reported in."""
        self._llm_reachable = bool(connected)
        self.refresh_readiness()

    def _selected_model(self) -> tuple[str, bool]:
        opts = self._transcribe_options
        key = opts.model_combo.currentData() if hasattr(opts, "model_combo") else None
        for model_key, label, downloaded in whisper_model_options_with_state():
            if model_key == key:
                return label, downloaded
        return (opts.model_combo.currentText() if hasattr(opts, "model_combo") else ""), True

    def _readiness_facts(self) -> ReadinessFacts:
        cfg = get_config()
        if self._ffmpeg_found is None:
            from core.external_tools import resolve_tool
            self._ffmpeg_found = resolve_tool("ffmpeg") is not None
        if self._diarize_installed is None:
            import importlib.util
            try:
                self._diarize_installed = importlib.util.find_spec("pyannote.audio") is not None
            except (ImportError, ValueError):
                self._diarize_installed = False
        provider = getattr(cfg, "yt_provider", "lmstudio") or "lmstudio"
        key_field = {"openai": "yt_openai_api_key", "anthropic": "yt_anthropic_api_key"}.get(provider)
        model_label, downloaded = self._selected_model()
        return ReadinessFacts(
            model_label=model_label,
            model_downloaded=downloaded,
            ffmpeg_found=bool(self._ffmpeg_found),
            llm_provider=provider,
            llm_reachable=self._llm_reachable,
            cloud_key_set=bool(key_field and getattr(cfg, key_field, "")),
            diarize_ready=bool(self._diarize_installed and getattr(cfg, "hf_token", None)),
        )

    def refresh_readiness(self) -> None:
        """Re-render the step chain and the readiness line for the
        selected recipe — cheap: no network, no heavy import."""
        if not hasattr(self, "_ready_layout"):
            return
        from application.steps import STEP_REGISTRY

        recipe = self._recipes.get(self._recipe_key) or TRANSCRIPT_ONLY
        steps = [name for name in recipe.steps if name in STEP_REGISTRY]
        self._steps_label.setText(
            "  →  ".join(tr(STEP_REGISTRY[name].label_key) for name in steps)
        )
        llm_steps = [d.name for d in STEP_REGISTRY.values() if d.resource == "local_llm"]
        checks = recipe_checks(steps, llm_steps, self._readiness_facts())

        while self._ready_layout.count():
            item = self._ready_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for check in checks:
            label = QLabel(f"{_CHECK_GLYPHS[check.state]} {tr(check.text_key, **check.params)}")
            label.setProperty("role", _CHECK_ROLES[check.state])
            label.setProperty("readiness", check.key)
            self._ready_layout.addWidget(label)
            if check.fix and check.state != OK:
                link = QPushButton(tr(f"ready_fix_{check.fix}"))
                link.setProperty("variant", "ghost")
                link.setProperty("role", "inline-link")
                link.clicked.connect(lambda _c=False, fix=check.fix: self.fix_requested.emit(fix))
                self._ready_layout.addWidget(link)
