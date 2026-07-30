"""
Whispered – YouTube Panel
Generates YouTube-ready titles, description, tags, and chapter timecodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QPlainTextEdit, QApplication, QTabWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from core.i18n import tr
from core.logger import get_logger
from core.paths import output_dir
from core.youtube_description import compose_full_description, format_youtube_description
from ui.toast import show_toast
from utils import language_name_for_code

logger = get_logger(__name__)


@dataclass(frozen=True)
class _TabSpec:
    """Everything that varies per generated-content tab, keyed once instead
    of via three separate parallel lists/dicts that had to be kept in sync
    by hand (insight type, filename suffix, and index -> widget mapping)."""
    insight_type: str    # matches core.insights_worker._INSIGHT_TYPES
    edit_attr: str        # instance attribute holding this tab's QPlainTextEdit
    file_key: str         # filename suffix used by _save_to_file
    label_key: str        # i18n key for the tab title
    mono: bool = True     # monospace font (all but the description tab)


# Order here is the order tabs are created/added in _setup_ui.
_TAB_SPECS: tuple[_TabSpec, ...] = (
    _TabSpec("chapters", "_chapters_edit", "chapters", "yt_tab_chapters"),
    _TabSpec("yt_titles", "_titles_edit", "titles", "yt_tab_titles"),
    _TabSpec("yt_description", "_desc_edit", "description", "yt_tab_description", mono=False),
    _TabSpec("yt_tags", "_tags_edit", "tags", "yt_tab_tags"),
    _TabSpec("yt_questions", "_questions_edit", "questions", "yt_tab_questions"),
)

_YT_TYPES = tuple(spec.insight_type for spec in _TAB_SPECS)

# Save location for generated files: the user data directory (same base as
# config.json/history.db), not a path under the app's own install location —
# in a PyInstaller bundle that location is read-only and saving would fail.
_OUTPUT_DIR = output_dir()


def _friendly_path(path: Path) -> str:
    """Shorten a path under $HOME to a ``~/...`` form for display in toasts."""
    try:
        return str(Path("~") / path.relative_to(Path.home()))
    except ValueError:
        return str(path)


class YouTubePanel(QWidget):
    """YouTube tab — generates titles, description, tags, and timecode chapters."""

    # Emitted once every tab's worker has settled (success = at least one
    # tab produced usable content). Lets a preset chain (MainWindow) know
    # when it's safe to move on without polling internal worker state.
    generation_finished = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments = []
        self._workers: dict = {}   # insight_type → worker
        self._pending = 0
        self._source_name = ""
        self._transcript_language: str | None = None
        self._description_text: str | None = None
        self._chapters_data: list | None = None
        self._any_success = False
        self._any_error = False
        self._setup_ui()

    # ── UI ──────────────────────────────────────────────────────────

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._placeholder = QLabel(tr("youtube_placeholder"))
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setProperty("role", "dim")
        self._placeholder.setStyleSheet("font-size: 13px;")
        layout.addWidget(self._placeholder)

        controls = QHBoxLayout()
        controls.setSpacing(8)

        self._lang_combo = QComboBox()
        self._lang_combo.addItem(tr("youtube_lang_auto"), None)
        self._lang_combo.addItem("Русский", "Russian")
        self._lang_combo.addItem("English", "English")
        controls.addWidget(self._lang_combo)

        self._provider_combo = QComboBox()
        self._provider_combo.addItem(tr("provider_lmstudio"), "lmstudio")
        self._provider_combo.addItem(tr("provider_openai"), "openai")
        self._provider_combo.addItem(tr("provider_anthropic"), "anthropic")
        self._provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        controls.addWidget(self._provider_combo)

        self._configure_btn = QPushButton(tr("provider_configure"))
        self._configure_btn.setEnabled(False)
        self._configure_btn.clicked.connect(self._open_provider_dialog)
        controls.addWidget(self._configure_btn)

        self._gen_btn = QPushButton(tr("youtube_generate"))
        self._gen_btn.setProperty("variant", "primary")
        self._gen_btn.setEnabled(False)
        self._gen_btn.clicked.connect(self._generate)
        controls.addWidget(self._gen_btn)

        controls.addStretch()

        self._copy_btn = QPushButton(tr("youtube_copy"))
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy_to_clipboard)
        controls.addWidget(self._copy_btn)

        self._save_btn = QPushButton(tr("youtube_save"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_to_file)
        controls.addWidget(self._save_btn)

        layout.addLayout(controls)

        self._privacy_notice = QLabel(tr("youtube_privacy_notice"))
        self._privacy_notice.setWordWrap(True)
        self._privacy_notice.setProperty("role", "warning-text")
        self._privacy_notice.setStyleSheet("font-size: 11px;")
        self._privacy_notice.setVisible(False)
        layout.addWidget(self._privacy_notice)

        self._init_provider_from_config()

        # Inner tabs: Chapters | Titles | Description | Tags | Key Questions
        self._tabs = QTabWidget()
        self._tabs.setVisible(False)

        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)

        for spec in _TAB_SPECS:
            edit = QPlainTextEdit()
            edit.setReadOnly(True)
            if spec.mono:
                edit.setFont(mono)
            setattr(self, spec.edit_attr, edit)
            self._tabs.addTab(edit, tr(spec.label_key))

        layout.addWidget(self._tabs, stretch=1)

    # ── Provider selection ─────────────────────────────────────────

    def _init_provider_from_config(self):
        from config import get_config
        kind = get_config().yt_provider
        idx = self._provider_combo.findData(kind)
        self._provider_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._on_provider_changed()

    def _on_provider_changed(self):
        from config import get_config, save_config
        kind = self._provider_combo.currentData()
        cfg = get_config()
        if cfg.yt_provider != kind:
            cfg.yt_provider = kind
            save_config()
        is_cloud = kind != "lmstudio"
        self._configure_btn.setEnabled(is_cloud)
        self._privacy_notice.setVisible(is_cloud)

    def _open_provider_dialog(self):
        from ui.provider_dialog import ProviderDialog
        kind = self._provider_combo.currentData()
        dialog = ProviderDialog(kind=kind, parent=self)
        dialog.exec()

    # ── Public API ──────────────────────────────────────────────────

    def set_segments(self, segments, transcript_language: str | None = None) -> None:
        self._segments = segments
        self._transcript_language = transcript_language
        self._gen_btn.setEnabled(bool(segments))
        if segments:
            self._placeholder.hide()
        else:
            self._placeholder.show()

    def set_source_name(self, name: str) -> None:
        """Base filename (no extension) used when saving generated files."""
        self._source_name = name or ""

    def shutdown(self) -> None:
        """Part of the Shutdownable protocol (ui/shutdownable.py). clear()
        already cancels in-flight workers with a bounded timeout, which is
        exactly what window close needs too."""
        self.clear()

    def clear(self) -> None:
        self._segments = []
        self._source_name = ""
        self._transcript_language = None
        self._gen_btn.setEnabled(False)
        self._copy_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._description_text = None
        self._chapters_data = None
        self._any_success = False
        self._any_error = False
        for edit in self._edits():
            edit.clear()
        self._tabs.setVisible(False)
        self._cancel_workers(timeout=2000)
        self._pending = 0
        self._placeholder.setText(tr("youtube_placeholder"))
        self._placeholder.show()

    def _cancel_workers(self, timeout: int) -> None:
        """Cancel, disconnect, and schedule deletion of all workers before
        dropping them.

        Disconnecting before wait() matters: if a worker doesn't finish
        within *timeout*, it keeps running in the background and would
        otherwise emit finished/error_occurred into a *new* generation
        run later, corrupting its _pending count and overwriting tabs
        with stale data.

        Every worker is constructed with parent=self, so losing the Python
        reference in self._workers alone does not free it — as a QObject
        child of this panel it stays alive (and shows up in
        findChildren(QThread)) until deleteLater() actually runs. Workers
        that already finished normally (isRunning() is False by the time
        the *next* _generate() call gets here) still need this, not just
        ones that were still running and had to be cancelled.
        """
        for w in self._workers.values():
            if not w:
                continue
            if w.isRunning():
                w.cancel()
                try:
                    w.finished.disconnect(self._on_finished)
                    w.error_occurred.disconnect(self._on_error)
                except (RuntimeError, TypeError):
                    pass
                w.wait(timeout)
            w.deleteLater()
        self._workers.clear()

    # ── Generation ──────────────────────────────────────────────────

    def generate(self) -> None:
        """Public trigger for programmatic (preset-chain) use — identical
        to clicking the Generate button."""
        self._generate()

    def _generate(self):
        from config import get_config
        from core.ai_provider import provider_from_config
        from core.insights_worker import InsightsWorker

        cfg = get_config()
        provider = provider_from_config(cfg)

        # Both misconfiguration paths still count as a settled run:
        # generation_finished must fire so a preset chain waiting on this
        # panel unblocks (shows its error toast, re-enables Process)
        # instead of hanging on a step that never started.
        if provider.kind != "lmstudio" and not provider.api_key:
            self._chapters_edit.setPlainText(tr("youtube_no_api_key"))
            self._tabs.setVisible(True)
            self._placeholder.hide()
            self.generation_finished.emit(False)
            return

        if provider.kind == "lmstudio" and not cfg.lm_studio_url:
            self._chapters_edit.setPlainText(tr("youtube_no_lm"))
            self._tabs.setVisible(True)
            self._placeholder.hide()
            self.generation_finished.emit(False)
            return

        # Cancel any in-progress workers
        self._cancel_workers(timeout=1000)

        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("youtube_generating"))
        self._copy_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._description_text = None
        self._chapters_data = None
        self._any_success = False
        self._any_error = False
        for edit in self._edits():
            edit.clear()
        self._tabs.setVisible(True)
        self._placeholder.hide()

        # "Auto" (None) falls back to the transcript's own detected
        # language rather than sending no directive at all — an empty
        # directive left the model free to answer in whatever language it
        # defaulted to (usually English), even for a Russian transcript.
        lang = self._lang_combo.currentData() or language_name_for_code(self._transcript_language)
        self._pending = len(_YT_TYPES)

        for yt_type in _YT_TYPES:
            worker = InsightsWorker(
                yt_type, self._segments, cfg.lm_studio_url, language=lang,
                provider=(None if provider.kind == "lmstudio" else provider),
                parent=self,
            )
            worker.finished.connect(self._on_finished)
            worker.error_occurred.connect(self._on_error)
            self._workers[yt_type] = worker
            worker.start()

    def _on_finished(self, insight_type: str, data):
        self._pending = max(0, self._pending - 1)
        self._any_success = True

        if insight_type == "chapters":
            if isinstance(data, list):
                self._chapters_data = data
                text = format_youtube_description(data)
                self._chapters_edit.setPlainText(text or tr("youtube_empty"))
            else:
                self._chapters_edit.setPlainText(str(data) if data else tr("youtube_empty"))
            self._maybe_compose_description()

        elif insight_type == "yt_titles":
            if isinstance(data, list):
                self._titles_edit.setPlainText("\n\n".join(f"{i+1}. {t}" for i, t in enumerate(data)))
            else:
                self._titles_edit.setPlainText(str(data) if data else "")

        elif insight_type == "yt_description":
            if isinstance(data, list) and data:
                self._description_text = data[0] if isinstance(data[0], str) else str(data[0])
                self._desc_edit.setPlainText(self._description_text)
            elif isinstance(data, str):
                self._description_text = data
                self._desc_edit.setPlainText(data)
            self._maybe_compose_description()

        elif insight_type == "yt_tags":
            if isinstance(data, list):
                self._tags_edit.setPlainText(", ".join(data))
            elif isinstance(data, str):
                self._tags_edit.setPlainText(data)

        elif insight_type == "yt_questions":
            if isinstance(data, list):
                text = format_youtube_description(data)
                self._questions_edit.setPlainText(text or tr("youtube_empty"))
            else:
                self._questions_edit.setPlainText(str(data) if data else tr("youtube_empty"))

        if self._pending == 0:
            self._finish_run()

    def _maybe_compose_description(self) -> None:
        """Once both the description and chapters are in, fold the chapter
        timecodes into the Description tab so it reads as one ready-to-paste
        YouTube description (hook + summary + "Timecodes:" + chapter list)."""
        full = compose_full_description(
            self._description_text, self._chapters_data, tr("youtube_timecodes_label")
        )
        if full and full != self._description_text:
            self._desc_edit.setPlainText(full)

    def _on_error(self, insight_type: str, msg: str):
        logger.warning("YouTube worker error (%s): %s", insight_type, msg)
        self._pending = max(0, self._pending - 1)
        self._any_error = True
        edit = self._type_to_edit(insight_type)
        if edit is not None:
            edit.setPlainText(f"{tr('youtube_error')}: {msg}")
        if self._pending == 0:
            self._finish_run()

    def _finish_run(self) -> None:
        """Called once all workers for a generation run have settled."""
        self._reset_button()
        if self._any_success:
            # At least one tab has usable content — let the user copy/save
            # it even if a sibling insight type failed.
            self._copy_btn.setEnabled(True)
            self._save_btn.setEnabled(True)
        if self._any_error:
            show_toast(self, tr("youtube_generate_error"), kind="error")
        self.generation_finished.emit(self._any_success)

    def _reset_button(self):
        self._gen_btn.setEnabled(bool(self._segments))
        self._gen_btn.setText(tr("youtube_generate"))

    def _edits(self) -> list[QPlainTextEdit]:
        """All tab edit widgets, in tab order."""
        return [getattr(self, spec.edit_attr) for spec in _TAB_SPECS]

    def _edit_for_index(self, idx: int) -> QPlainTextEdit:
        if 0 <= idx < len(_TAB_SPECS):
            return getattr(self, _TAB_SPECS[idx].edit_attr)
        return self._chapters_edit

    def _type_to_edit(self, insight_type: str) -> QPlainTextEdit | None:
        for spec in _TAB_SPECS:
            if spec.insight_type == insight_type:
                return getattr(self, spec.edit_attr)
        return None

    def _copy_to_clipboard(self):
        """Copy the content of the currently-visible inner tab."""
        edit = self._edit_for_index(self._tabs.currentIndex())
        text = edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            show_toast(self, tr("youtube_copied"), kind="success")

    def _save_to_file(self):
        """Save the currently-visible inner tab to output/ in the project."""
        idx = self._tabs.currentIndex()
        edit = self._edit_for_index(idx)
        text = edit.toPlainText()
        if not text:
            return

        key = _TAB_SPECS[idx].file_key if 0 <= idx < len(_TAB_SPECS) else "youtube"
        try:
            path = self._write_tab_file(_OUTPUT_DIR, key, text)
        except OSError as exc:
            logger.warning("Failed to save YouTube file to %s: %s", _OUTPUT_DIR, exc)
            show_toast(self, tr("youtube_save_error"), kind="error")
            return

        show_toast(self, tr("youtube_saved", path=_friendly_path(path)), kind="success")

    def save_all(self, output_dir: Path) -> list[Path]:
        """Save every tab with generated content to *output_dir*. Used by
        the preset-chain auto-save step (see MainWindow._finish_preset_chain);
        unlike _save_to_file, saves all tabs at once rather than just the
        currently-visible one, and doesn't show a toast (the caller shows
        one summarizing the whole chain)."""
        saved = []
        for spec in _TAB_SPECS:
            text = getattr(self, spec.edit_attr).toPlainText()
            if not text:
                continue
            try:
                saved.append(self._write_tab_file(output_dir, spec.file_key, text))
            except OSError as exc:
                logger.warning("Failed to save %s to %s: %s", spec.file_key, output_dir, exc)
        return saved

    def _write_tab_file(self, directory: Path, file_key: str, text: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stem = self._source_name or "youtube"
        path = directory / f"{stem}_{file_key}.txt"
        path.write_text(text, encoding="utf-8")
        return path
