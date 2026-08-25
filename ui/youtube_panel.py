"""
Whispered – YouTube Panel
Generates YouTube-ready titles, description, tags, and chapter timecodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QPlainTextEdit, QApplication, QToolBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont

from core.i18n import tr
from core.logger import get_logger
from core.paths import output_dir
from core.youtube_description import compose_full_description, format_youtube_description
from ui.toast import show_toast

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

    # Emitted by set_result()/set_error() once MainWindow's "youtube_package"
    # JobRunner has settled. Lets a preset chain (MainWindow) know when it's
    # safe to move on without polling internal job state.
    generate_requested = pyqtSignal()
    generation_finished = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments = []
        self._source_name = ""
        self._transcript_language: str | None = None
        self._description_text: str | None = None
        self._chapters_data: list | None = None
        # Set via set_provenance() by MainWindow whenever the open
        # transcript changes — recorded into each saved file's Artifact
        # manifest (see core.paths.artifact_dir / R5-full in the audit plan).
        self._record_id: int | None = None
        self._source_path: str | None = None
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
        self._gen_btn.clicked.connect(self.generate_requested.emit)
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

        # Shown whenever a generation run comes back empty-handed (LM
        # Studio unreachable, missing cloud API key, or every worker
        # erroring out) so the user has an explicit way to retry once the
        # underlying problem is fixed, without hunting for the Generate
        # button again — this matters most when the run was kicked off
        # automatically by a preset chain and the user is on another tab.
        retry_row = QHBoxLayout()
        retry_row.setSpacing(8)
        self._retry_label = QLabel()
        self._retry_label.setWordWrap(True)
        self._retry_label.setProperty("role", "warning-text")
        self._retry_label.setStyleSheet("font-size: 11px;")
        retry_row.addWidget(self._retry_label, stretch=1)
        self._retry_btn = QPushButton(tr("youtube_retry"))
        self._retry_btn.clicked.connect(self.generate_requested.emit)
        retry_row.addWidget(self._retry_btn)
        self._retry_bar = QWidget()
        self._retry_bar.setLayout(retry_row)
        self._retry_bar.setVisible(False)
        layout.addWidget(self._retry_bar)

        self._init_provider_from_config()

        # Inner tabs: Chapters | Titles | Description | Tags | Key Questions
        self._tabs = QToolBox()
        self._tabs.setVisible(False)

        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)

        for spec in _TAB_SPECS:
            edit = QPlainTextEdit()
            edit.setReadOnly(True)
            if spec.mono:
                edit.setFont(mono)
            setattr(self, spec.edit_attr, edit)
            self._tabs.addItem(edit, tr(spec.label_key))

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

    def set_provenance(self, record_id: int | None, source_path: str | None) -> None:
        """Called by MainWindow whenever the open transcript's identity
        changes — recorded into each saved file's Artifact manifest."""
        self._record_id = record_id
        self._source_path = source_path

    def selected_language(self) -> str | None:
        """Explicit language directive from the combo — ``None`` means
        "auto", i.e. fall back to the transcript's own detected language
        (MainWindow's _start_youtube_job() does that fallback, the same
        way this panel used to)."""
        return self._lang_combo.currentData()

    def shutdown(self) -> None:
        """Part of the Shutdownable protocol (ui/shutdownable.py). This
        panel no longer owns any worker — the "youtube_package" JobRunner
        it triggers via generate_requested lives on MainWindow now (see
        docs/UI_REDESIGN_PLAN_2026-09.ru.md, B5d) and is shut down there,
        the same way _clean_job/_article_job/_insights_job already are."""
        self.clear()

    def clear(self) -> None:
        self._segments = []
        self._source_name = ""
        self._transcript_language = None
        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("youtube_generate"))
        self._copy_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._description_text = None
        self._chapters_data = None
        for edit in self._edits():
            edit.clear()
        self._tabs.setVisible(False)
        self._retry_bar.setVisible(False)
        self._placeholder.setText(tr("youtube_placeholder"))
        self._placeholder.show()

    # ── Generation ──────────────────────────────────────────────────
    # This panel no longer runs anything itself — generate_requested asks
    # MainWindow to run the "youtube_package" step via JobRunner
    # (application/steps.py), and begin_generating()/set_result()/
    # set_error() below are its side of that: busy-state before the job
    # starts, and the two ways it can end.

    def generate(self) -> None:
        """Public trigger for programmatic (preset-chain) use — identical
        to clicking the Generate button."""
        self.generate_requested.emit()

    def begin_generating(self) -> None:
        self._gen_btn.setEnabled(False)
        self._gen_btn.setText(tr("youtube_generating"))
        self._copy_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._description_text = None
        self._chapters_data = None
        for edit in self._edits():
            edit.clear()
        self._tabs.setVisible(True)
        self._placeholder.hide()
        self._retry_bar.setVisible(False)

    def set_result(self, payload: dict) -> None:
        """*payload* is application/steps.py's "youtube_package" step
        output: ``{"chapters": [...], "yt_titles": [...],
        "yt_description": [...], "yt_tags": [...], "yt_questions": [...]}``."""
        data = payload.get("chapters")
        if isinstance(data, list):
            self._chapters_data = data
            text = format_youtube_description(data)
            self._chapters_edit.setPlainText(text or tr("youtube_empty"))
        else:
            self._chapters_edit.setPlainText(str(data) if data else tr("youtube_empty"))

        titles = payload.get("yt_titles")
        if isinstance(titles, list):
            self._titles_edit.setPlainText(
                "\n\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
            )
        else:
            self._titles_edit.setPlainText(str(titles) if titles else "")

        desc = payload.get("yt_description")
        if isinstance(desc, list) and desc:
            self._description_text = desc[0] if isinstance(desc[0], str) else str(desc[0])
            self._desc_edit.setPlainText(self._description_text)
        elif isinstance(desc, str):
            self._description_text = desc
            self._desc_edit.setPlainText(desc)
        self._maybe_compose_description()

        tags = payload.get("yt_tags")
        if isinstance(tags, list):
            self._tags_edit.setPlainText(", ".join(tags))
        elif isinstance(tags, str):
            self._tags_edit.setPlainText(tags)

        questions = payload.get("yt_questions")
        if isinstance(questions, list):
            text = format_youtube_description(questions)
            self._questions_edit.setPlainText(text or tr("youtube_empty"))
        else:
            self._questions_edit.setPlainText(str(questions) if questions else tr("youtube_empty"))

        self._reset_button()
        self._copy_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self.generation_finished.emit(True)

    def set_error(self, message: str) -> None:
        """Covers both a misconfigured provider (no API key / no LM Studio
        URL) and a real job failure — both used to be handled separately
        (the former skipped the "generate_error" toast); unified here
        since both now flow through the same one-step JobRunner and a
        precheck failure deserves the same visibility a runtime one gets."""
        logger.warning("YouTube job failed: %s", message)
        self._chapters_edit.setPlainText(f"{tr('youtube_error')}: {message}" if message else "")
        self._tabs.setVisible(True)
        self._placeholder.hide()
        self._reset_button()
        show_toast(self, tr("youtube_generate_error"), kind="error")
        self._show_retry(message or tr("youtube_generate_error"))
        self.generation_finished.emit(False)

    def _maybe_compose_description(self) -> None:
        """Once both the description and chapters are in, fold the chapter
        timecodes into the Description tab so it reads as one ready-to-paste
        YouTube description (hook + summary + "Timecodes:" + chapter list)."""
        full = compose_full_description(
            self._description_text, self._chapters_data, tr("youtube_timecodes_label")
        )
        if full and full != self._description_text:
            self._desc_edit.setPlainText(full)

    def _reset_button(self):
        self._gen_btn.setEnabled(bool(self._segments))
        self._gen_btn.setText(tr("youtube_generate"))

    def _show_retry(self, reason: str) -> None:
        """Surface an inline retry bar so a failed run — e.g. LM Studio was
        unreachable when a preset chain kicked this off in the background —
        can be re-run with one click once the user notices and switches to
        this tab, instead of them having to rediscover the Generate button."""
        self._retry_label.setText(f"{reason} {tr('youtube_retry_hint')}")
        self._retry_bar.setVisible(True)

    def _edits(self) -> list[QPlainTextEdit]:
        """All tab edit widgets, in tab order."""
        return [getattr(self, spec.edit_attr) for spec in _TAB_SPECS]

    def _edit_for_index(self, idx: int) -> QPlainTextEdit:
        if 0 <= idx < len(_TAB_SPECS):
            return getattr(self, _TAB_SPECS[idx].edit_attr)
        return self._chapters_edit

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
        self._write_provenance(path, file_key)
        return path

    def _write_provenance(self, path: Path, file_key: str) -> None:
        """Record an Artifact manifest for a saved YouTube file (see
        docs/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R5-full step 3) — same
        mechanism already used for Cover and article exports. Best-effort:
        the .txt file is already safely on disk by the time this runs, so
        a manifest failure must not turn a successful save into an error.
        """
        try:
            from application.artifact_provenance import source_fingerprint, transcript_revision
            from domain.artifact import Artifact
            from infrastructure.persistence import artifact_store

            artifact_store.save(Artifact(
                record_id=str(self._record_id) if self._record_id is not None else "unsaved",
                source_hash=source_fingerprint(self._source_path),
                source_path=self._source_path or "",
                transcript_revision=transcript_revision(self._segments, self._transcript_language or ""),
                type=f"youtube_{file_key}",
                path=str(path),
            ))
        except Exception as exc:
            logger.warning("Failed to write YouTube artifact manifest for %s: %s", path, exc)
