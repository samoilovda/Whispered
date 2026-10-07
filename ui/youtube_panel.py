"""
Whispered – YouTube Panel
Generates YouTube-ready titles, description, tags, and chapter timecodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QPlainTextEdit, QApplication, QToolBox, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor

from core.i18n import tr
from ui.i18n_helpers import Retranslator
from core.logger import get_logger
from application.user_edits import (
    drop_edit,
    is_stale,
    load_overlay,
    overlay_path,
    rebase_edit,
    save_overlay,
    set_edit,
)
from core.paths import artifact_dir, output_dir
from core.youtube_description import (
    ISSUE_DUPLICATE,
    ISSUE_FIRST_MOVED,
    ISSUE_INVALID,
    ISSUE_LONG,
    ISSUE_PAST_END,
    ISSUE_TOO_CLOSE,
    LONG_CHAPTER_SECONDS,
    MIN_YOUTUBE_CHAPTERS,
    ChapterCheck,
    ChapterIssue,
    check_chapters,
    compose_full_description,
    format_chapter_lines,
    format_youtube_description,
    format_youtube_timestamp,
    parse_chapter_lines,
)
from ui.components import ChapterRow
from ui.theme import set_role
from ui.toast import show_toast

logger = get_logger(__name__)

# How many dropped/out-of-range chapters the status line names individually
# before collapsing the rest into "and N more".
_MAX_LISTED_CHAPTERS = 3


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

# Config.yt_language holds the name the prompt asks for; YouTube's
# snippet.defaultLanguage wants a code.
_LANGUAGE_CODES = {"Russian": "ru", "English": "en"}

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
    """YouTube tab — shows the youtube_package step's result: chapters,
    titles, description, tags and key questions.

    A pure viewer (B5): provider and output language live in Settings, and
    the step runs from File > Create YouTube package / Ctrl+K. The panel's
    only way to start it is ``_run_link``, which asks MainWindow for that
    same action through ``generate_requested``."""

    # generate_requested: the run link was clicked — MainWindow runs the
    # same single-step job as the menu action. generation_finished: emitted
    # by set_result()/set_error() once MainWindow's "youtube_package"
    # JobRunner has settled.
    generate_requested = pyqtSignal()
    generation_finished = pyqtSignal(bool)
    # A chapter's time was clicked: seconds to move the player to.
    seek_requested = pyqtSignal(int)
    publish_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments = []
        self._source_name = ""
        self._transcript_language: str | None = None
        self._description_text: str | None = None
        self._chapters_data: list | None = None
        # What check_chapters() made of the last chapter list, kept so the
        # status line can be rebuilt in a new language without regenerating.
        self._chapter_check: ChapterCheck | None = None
        self._chapter_duration: float | None = None
        # The step's own chapter list, and the user's edits kept apart from
        # it (application/user_edits.py — youtube_package.user.json). What
        # is shown, copied and published is the edit when there is one.
        self._model_chapters: list | None = None
        self._questions: list = []
        self._overlay: dict[str, Any] = {}
        self._editing_chapters = False
        self._bad_chapter_lines: list[int] = []
        # Set by MainWindow: the player's position, for "Insert player time".
        self._position_provider: Callable[[], float] | None = None
        # Set via set_provenance() by MainWindow whenever the open
        # transcript changes — recorded into each saved file's Artifact
        # manifest (see core.paths.artifact_dir / R5-full in the audit plan).
        self._record_id: int | None = None
        self._source_path: str | None = None
        # "empty" (nothing to work from), "ready" (transcript, no package),
        # "generating", "error" or "done" — drives the state row below.
        self._state = "empty"
        self._error_reason = ""
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.call(self._retranslate_youtube)
        self._i18n.bind()

    def _retranslate_youtube(self) -> None:
        self._copy_btn.setText(tr("youtube_copy"))
        self._save_btn.setText(tr("youtube_save"))
        self._publish_btn.setText(tr("yt_publish_btn"))
        for i, spec in enumerate(_TAB_SPECS):
            self._tabs.setItemText(i, tr(spec.label_key))
        self._placeholder.setText(tr("youtube_placeholder"))
        self._chapters_edit_btn.setText(tr("yt_chapters_edit"))
        self._chapters_insert_btn.setText(tr("yt_chapters_insert_time"))
        self._chapters_cancel_btn.setText(tr("yt_chapters_cancel"))
        self._chapters_done_btn.setText(tr("yt_chapters_done"))
        self._chapters_keep_btn.setText(tr("yt_chapters_keep_mine"))
        self._render_state()
        self._render_chapter_status()
        self._render_chapter_editing()

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
        controls.addStretch()

        self._copy_btn = QPushButton(tr("youtube_copy"))
        self._copy_btn.setEnabled(False)
        self._copy_btn.clicked.connect(self._copy_to_clipboard)
        controls.addWidget(self._copy_btn)

        self._save_btn = QPushButton(tr("youtube_save"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_to_file)
        controls.addWidget(self._save_btn)

        self._publish_btn = QPushButton(tr("yt_publish_btn"))
        self._publish_btn.setEnabled(False)
        self._publish_btn.clicked.connect(self.publish_requested.emit)
        controls.addWidget(self._publish_btn)

        layout.addLayout(controls)

        # Where the package stands when there is none to show yet: not
        # made, being made, or failed — with the one link that runs the
        # step (the same action as File > Create YouTube package). A
        # failed run kicked off by a recipe in the background is surfaced
        # here too, so the user can retry once they notice.
        state_row = QHBoxLayout()
        state_row.setSpacing(8)
        self._state_label = QLabel()
        self._state_label.setWordWrap(True)
        self._state_label.setProperty("role", "dim")
        self._state_label.setStyleSheet("font-size: 11px;")
        state_row.addWidget(self._state_label, stretch=1)
        self._run_link = QPushButton()
        self._run_link.clicked.connect(self.generate_requested.emit)
        state_row.addWidget(self._run_link)
        self._state_bar = QWidget()
        self._state_bar.setLayout(state_row)
        self._state_bar.setVisible(False)
        layout.addWidget(self._state_bar)

        # What YouTube will do with the chapter list: shown once chapters
        # arrive, so a dropped chapter or a list too short for YouTube to
        # display at all is visible here instead of only in the log.
        self._chapter_status = QLabel()
        self._chapter_status.setWordWrap(True)
        self._chapter_status.setProperty("role", "success-text")
        self._chapter_status.setStyleSheet("font-size: 11px;")
        self._chapter_status.setVisible(False)
        layout.addWidget(self._chapter_status)

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
            if spec.insight_type == "chapters":
                self._tabs.addItem(self._build_chapters_section(edit), tr(spec.label_key))
            else:
                self._tabs.addItem(edit, tr(spec.label_key))

        layout.addWidget(self._tabs, stretch=1)
        # Takes the slack only while the sections are hidden, so the state
        # row sits under the buttons instead of floating mid-panel; once the
        # sections show, their stretch factor wins.
        layout.addStretch()

    def _build_chapters_section(self, edit: QPlainTextEdit) -> QWidget:
        """Clickable chapter rows (as YouTube will list them) over the
        plain-text form. The rows show whenever there are chapters; the
        text edit stays the source for Copy/Save and is what shows instead
        when there is only a message (no chapters, an error)."""
        self._chapter_rows = QWidget()
        self._chapter_rows_layout = QVBoxLayout(self._chapter_rows)
        self._chapter_rows_layout.setContentsMargins(4, 4, 4, 4)
        self._chapter_rows_layout.setSpacing(0)
        self._chapter_rows_layout.addStretch()

        self._chapter_scroll = QScrollArea()
        self._chapter_scroll.setWidgetResizable(True)
        self._chapter_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._chapter_scroll.setWidget(self._chapter_rows)
        self._chapter_scroll.setVisible(False)

        # Edit controls and the note about the user's edits. The text edit
        # itself is the editor: chapters are written the way YouTube reads
        # them, one "M:SS Title" line each.
        self._chapters_note = QLabel()
        self._chapters_note.setWordWrap(True)
        self._chapters_note.setProperty("role", "muted")
        self._chapters_note.setStyleSheet("font-size: 11px;")
        self._chapters_reset_btn = QPushButton(tr("yt_chapters_reset"))
        self._chapters_reset_btn.clicked.connect(self._take_model_chapters)
        self._chapters_keep_btn = QPushButton(tr("yt_chapters_keep_mine"))
        self._chapters_keep_btn.clicked.connect(self._keep_user_chapters)
        self._chapters_edit_btn = QPushButton(tr("yt_chapters_edit"))
        self._chapters_edit_btn.clicked.connect(self._begin_chapter_edit)
        self._chapters_insert_btn = QPushButton(tr("yt_chapters_insert_time"))
        self._chapters_insert_btn.clicked.connect(self._insert_player_time)
        self._chapters_cancel_btn = QPushButton(tr("yt_chapters_cancel"))
        self._chapters_cancel_btn.clicked.connect(self._cancel_chapter_edit)
        self._chapters_done_btn = QPushButton(tr("yt_chapters_done"))
        self._chapters_done_btn.setProperty("variant", "primary")
        self._chapters_done_btn.clicked.connect(self._finish_chapter_edit)
        bar = QHBoxLayout()
        bar.setContentsMargins(4, 4, 4, 0)
        bar.setSpacing(6)
        bar.addWidget(self._chapters_note, stretch=1)
        for button in (
            self._chapters_reset_btn, self._chapters_keep_btn, self._chapters_edit_btn,
            self._chapters_insert_btn, self._chapters_cancel_btn, self._chapters_done_btn,
        ):
            button.setVisible(False)
            bar.addWidget(button)

        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.addLayout(bar)
        box.addWidget(self._chapter_scroll)
        box.addWidget(edit)
        return section

    def _render_chapter_rows(self) -> None:
        layout = self._chapter_rows_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                # Detach now: deleteLater() alone leaves the old row in the
                # tree (and in findChildren) until the event loop runs.
                widget.setParent(None)
                widget.deleteLater()
        chapters = self._chapter_check.chapters if self._chapter_check else ()
        for start, title in chapters:
            row = ChapterRow(start, title, self._chapter_rows, label=format_youtube_timestamp(start))
            row.seek_requested.connect(self.seek_requested)
            layout.addWidget(row)
        layout.addStretch()
        show_rows = bool(chapters) and not self._editing_chapters
        self._chapter_scroll.setVisible(show_rows)
        self._chapters_edit.setVisible(not show_rows)

    # ── Public API ──────────────────────────────────────────────────

    def set_segments(self, segments, transcript_language: str | None = None) -> None:
        self._segments = segments
        self._transcript_language = transcript_language
        if self._state in ("empty", "ready"):
            self._set_state("ready" if segments else "empty")

    def set_source_name(self, name: str) -> None:
        """Base filename (no extension) used when saving generated files."""
        self._source_name = name or ""

    def set_provenance(self, record_id: int | None, source_path: str | None) -> None:
        """Called by MainWindow whenever the open transcript's identity
        changes — recorded into each saved file's Artifact manifest."""
        self._record_id = record_id
        self._source_path = source_path

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
        self._copy_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._publish_btn.setEnabled(False)
        self._description_text = None
        self._chapters_data = None
        self._reset_chapter_edits()
        self._set_chapter_check(None)
        for edit in self._edits():
            edit.clear()
        self._tabs.setVisible(False)
        self._set_state("empty")

    # ── Generation ──────────────────────────────────────────────────
    # This panel no longer runs anything itself — generate_requested asks
    # MainWindow to run the "youtube_package" step via JobRunner
    # (application/steps.py), and begin_generating()/set_result()/
    # set_error() below are its side of that: busy-state before the job
    # starts, and the two ways it can end.

    def generate(self) -> None:
        """Public trigger for programmatic use — identical to clicking the
        run link."""
        self.generate_requested.emit()

    def begin_generating(self) -> None:
        self._copy_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._publish_btn.setEnabled(False)
        self._description_text = None
        self._chapters_data = None
        self._reset_chapter_edits()
        self._set_chapter_check(None)
        for edit in self._edits():
            edit.clear()
        self._tabs.setVisible(True)
        self._set_state("generating")

    def set_result(self, payload: dict) -> None:
        """*payload* is application/steps.py's "youtube_package" step
        output: ``{"chapters": [...], "yt_titles": [...],
        "yt_description": [...], "yt_tags": [...], "yt_questions": [...]}``."""
        data = payload.get("chapters")
        self._editing_chapters = False
        self._bad_chapter_lines = []
        if isinstance(data, list):
            self._model_chapters = data
            self._overlay = load_overlay(self._overlay_file())
            self._apply_chapters()
        else:
            self._model_chapters = None
            self._overlay = {}
            self._chapters_data = None
            self._chapters_edit.setPlainText(str(data) if data else tr("youtube_empty"))
            self._set_chapter_check(None)
        self._render_chapter_editing()

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
            # Questions are not chapters: keep each one's own time — the
            # chapter formatter would move the first to 0:00 and drop the
            # ones closer than 10 s, and a 0:00-led list in the description
            # could be taken by YouTube for chapters.
            self._questions = questions
            text = format_chapter_lines(questions)
            self._questions_edit.setPlainText(text or tr("youtube_empty"))
        else:
            self._questions = []
            self._questions_edit.setPlainText(str(questions) if questions else tr("youtube_empty"))

        self._set_state("done")
        self._copy_btn.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._publish_btn.setEnabled(True)
        self.generation_finished.emit(True)

    def set_error(self, message: str) -> None:
        """Covers both a misconfigured provider (no API key / no LM Studio
        URL) and a real job failure — both used to be handled separately
        (the former skipped the "generate_error" toast); unified here
        since both now flow through the same one-step JobRunner and a
        precheck failure deserves the same visibility a runtime one gets."""
        logger.warning("YouTube job failed: %s", message)
        self._reset_chapter_edits()
        self._chapters_edit.setPlainText(f"{tr('youtube_error')}: {message}" if message else "")
        self._set_chapter_check(None)
        self._tabs.setVisible(True)
        show_toast(self, tr("youtube_generate_error"), kind="error")
        self._error_reason = message or tr("youtube_generate_error")
        self._set_state("error")
        self.generation_finished.emit(False)

    def publish_texts(self) -> dict:
        """The texts as they currently stand in the tabs — including the
        user's edits and the description with chapter timecodes folded in —
        for the publish dialog. ``titles`` are raw tab lines (numbering and
        all; ``application.youtube_publish.normalize_titles`` cleans them),
        ``chapter_check`` is the last ``check_chapters()`` result or None.
        Empty strings/lists when nothing was generated yet."""
        return {
            "titles": self._titles_edit.toPlainText().splitlines(),
            "description": self._desc_edit.toPlainText(),
            "tags": self._tags_edit.toPlainText(),
            "language": self._publish_language(),
            "chapter_check": self._chapter_check,
        }

    def _publish_language(self) -> str | None:
        """Config.yt_language as a code, else the transcript's language."""
        from config import get_config
        return _LANGUAGE_CODES.get(get_config().yt_language, self._transcript_language)

    def provenance(self) -> tuple[int | None, str | None]:
        """``(record_id, source_path)`` as last set by ``set_provenance()``."""
        return self._record_id, self._source_path

    def has_publishable_content(self) -> bool:
        """True once a title and a description exist to publish."""
        return bool(
            self._titles_edit.toPlainText().strip()
            and self._desc_edit.toPlainText().strip()
        )

    def _maybe_compose_description(self) -> None:
        """Once both the description and chapters are in, fold the chapter
        timecodes into the Description tab so it reads as one ready-to-paste
        YouTube description (hook + summary + "Timecodes:" + chapter list).
        Re-run after a chapter edit, so the description follows it."""
        full = compose_full_description(
            self._description_text, self._chapters_data, tr("youtube_timecodes_label")
        )
        if full:
            self._desc_edit.setPlainText(full)

    # ── Chapter edits ───────────────────────────────────────────────
    # The user's chapters are an overlay on the step's result, never
    # written into youtube_package.json: the step's cache compares against
    # that file, and a hand edit is not model output.

    def set_position_provider(self, provider: Callable[[], float] | None) -> None:
        """Where "Insert player time" reads the playback position from."""
        self._position_provider = provider
        self._render_chapter_editing()

    def _overlay_file(self) -> Path:
        """The edit overlay beside youtube_package.json — the same
        artifact_dir() MainWindow gives the youtube_package step
        (record id or "unsaved", source path or "recording")."""
        record_id = self._record_id if self._record_id is not None else "unsaved"
        folder = artifact_dir(record_id, self._source_path or "recording")
        return overlay_path(folder / "youtube_package.json")

    def _effective_chapters(self) -> list:
        edited = self._overlay.get("chapters")
        if isinstance(edited, list):
            return edited
        return self._model_chapters or []

    def _apply_chapters(self) -> None:
        """Show the effective chapters everywhere: rows, Copy/Save text,
        checks, and (via _maybe_compose_description) the description."""
        chapters = self._effective_chapters()
        self._chapters_data = chapters
        self._chapters_edit.setReadOnly(True)
        text = format_youtube_description(chapters)
        self._chapters_edit.setPlainText(text or tr("youtube_empty"))
        # Plain-dict segments (some tests) carry no usable duration;
        # the past-end check is then simply skipped.
        duration = getattr(self._segments[-1], "end", None) if self._segments else None
        self._set_chapter_check(check_chapters(chapters, duration), duration)

    def _store_overlay(self, overlay: dict[str, Any]) -> None:
        self._overlay = overlay
        try:
            save_overlay(self._overlay_file(), overlay)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to save YouTube chapter edits: %s", exc)
            show_toast(self, tr("yt_chapters_save_error"), kind="error")

    def _begin_chapter_edit(self) -> None:
        self._editing_chapters = True
        self._bad_chapter_lines = []
        self._chapters_edit.setReadOnly(False)
        self._chapters_edit.setPlainText(format_chapter_lines(self._effective_chapters()))
        # Cursor at the end, so "Insert player time" appends a new line.
        self._chapters_edit.moveCursor(QTextCursor.MoveOperation.End)
        self._render_chapter_rows()
        self._render_chapter_editing()
        self._chapters_edit.setFocus()

    def _cancel_chapter_edit(self) -> None:
        self._editing_chapters = False
        self._bad_chapter_lines = []
        self._apply_chapters()
        self._render_chapter_editing()

    def _finish_chapter_edit(self) -> None:
        chapters, bad = parse_chapter_lines(self._chapters_edit.toPlainText())
        if bad:
            self._bad_chapter_lines = bad
            self._render_chapter_editing()
            return
        chapters.sort(key=lambda item: item["start"])
        model = self._model_chapters or []
        # Compare in the same normal form, so re-saving the model's own
        # chapters unchanged records no edit.
        model_normal, _ = parse_chapter_lines(format_chapter_lines(model))
        if chapters == model_normal:
            overlay = drop_edit(self._overlay, "chapters")
        else:
            overlay = set_edit(self._overlay, "chapters", chapters, model)
        self._store_overlay(overlay)
        self._editing_chapters = False
        self._bad_chapter_lines = []
        self._apply_chapters()
        self._maybe_compose_description()
        self._render_chapter_editing()

    def _insert_player_time(self) -> None:
        if self._position_provider is None:
            return
        stamp = format_youtube_timestamp(int(self._position_provider()))
        cursor = self._chapters_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
        prefix = "\n" if cursor.block().text().strip() else ""
        cursor.insertText(f"{prefix}{stamp} ")
        self._chapters_edit.setTextCursor(cursor)
        self._chapters_edit.setFocus()

    def _take_model_chapters(self) -> None:
        self._store_overlay(drop_edit(self._overlay, "chapters"))
        self._apply_chapters()
        self._maybe_compose_description()
        self._render_chapter_editing()

    def _keep_user_chapters(self) -> None:
        self._store_overlay(rebase_edit(self._overlay, "chapters", self._model_chapters or []))
        self._render_chapter_editing()

    def _render_chapter_editing(self) -> None:
        """Edit buttons and the edits note for the current state — also
        called on a language change."""
        editing = self._editing_chapters
        has_model = self._model_chapters is not None
        edited = "chapters" in self._overlay and has_model
        stale = edited and is_stale(self._overlay, "chapters", self._model_chapters)

        self._chapters_edit_btn.setVisible(has_model and not editing)
        self._chapters_insert_btn.setVisible(editing)
        self._chapters_insert_btn.setEnabled(self._position_provider is not None)
        self._chapters_cancel_btn.setVisible(editing)
        self._chapters_done_btn.setVisible(editing)
        self._chapters_reset_btn.setVisible(edited and not editing)
        self._chapters_keep_btn.setVisible(stale and not editing)
        self._chapters_reset_btn.setText(
            tr("yt_chapters_take_model") if stale else tr("yt_chapters_reset")
        )

        if editing and self._bad_chapter_lines:
            text = tr("yt_chapters_bad_lines", lines=", ".join(map(str, self._bad_chapter_lines)))
            role = "warning-text"
        elif editing:
            text, role = tr("yt_chapters_edit_hint"), "muted"
        elif stale:
            text, role = tr("yt_chapters_stale"), "warning-text"
        elif edited:
            text, role = tr("yt_chapters_edited"), "muted"
        else:
            text, role = "", "muted"
        set_role(self._chapters_note, role)
        self._chapters_note.setText(text)

    def _reset_chapter_edits(self) -> None:
        """Forget the shown package's chapters and edit state (the overlay
        file stays on disk and is read again with the next result)."""
        self._model_chapters = None
        self._overlay = {}
        self._editing_chapters = False
        self._bad_chapter_lines = []
        self._chapters_edit.setReadOnly(True)
        self._render_chapter_editing()

    def _set_chapter_check(
        self, check: ChapterCheck | None, duration: float | None = None,
    ) -> None:
        self._chapter_check = check
        self._chapter_duration = duration
        self._render_chapter_rows()
        self._render_chapter_status()

    def _render_chapter_status(self) -> None:
        """Rebuild the chapter status line from the stored check — also
        called on a language change, hence no work beyond formatting."""
        check = self._chapter_check
        if check is None:
            self._chapter_status.clear()
            self._chapter_status.setVisible(False)
            return

        if check.shows_on_youtube:
            lines = [tr("yt_chapters_ok", count=len(check.chapters))]
        else:
            lines = [tr(
                "yt_chapters_too_few",
                count=len(check.chapters), minimum=MIN_YOUTUBE_CHAPTERS,
            )]

        invalid = check.issues_of(ISSUE_INVALID)
        if invalid:
            lines.append(tr("yt_chapters_invalid", count=len(invalid)))
        dropped = check.issues_of(ISSUE_DUPLICATE, ISSUE_TOO_CLOSE)
        if dropped:
            lines.append(tr("yt_chapters_dropped", items=self._describe_chapters(dropped)))
        past_end = check.issues_of(ISSUE_PAST_END)
        if past_end and self._chapter_duration:
            lines.append(tr(
                "yt_chapters_past_end",
                duration=format_youtube_timestamp(int(self._chapter_duration)),
                items=self._describe_chapters(past_end),
            ))
        moved = check.issues_of(ISSUE_FIRST_MOVED)
        if moved:
            lines.append(tr(
                "yt_chapters_first_moved", time=format_youtube_timestamp(moved[0].seconds),
            ))
        long_chapters = check.issues_of(ISSUE_LONG)
        if long_chapters:
            lines.append(tr(
                "yt_chapters_long",
                count=len(long_chapters),
                limit=format_youtube_timestamp(LONG_CHAPTER_SECONDS),
            ))

        # The headline's ✓/✕ glyph carries the verdict on its own; colour
        # only says whether there is anything below it worth reading.
        if not check.shows_on_youtube:
            role = "danger-text"
        elif len(lines) > 1:
            role = "warning-text"
        else:
            role = "success-text"
        set_role(self._chapter_status, role)
        self._chapter_status.setText("\n".join(lines))
        self._chapter_status.setVisible(True)

    @staticmethod
    def _describe_chapters(issues: tuple[ChapterIssue, ...]) -> str:
        parts = []
        for issue in issues[:_MAX_LISTED_CHAPTERS]:
            time = format_youtube_timestamp(issue.start or 0)
            if issue.kind == ISSUE_TOO_CLOSE:
                parts.append(tr(
                    "yt_chapter_too_close", time=time, title=issue.title, seconds=issue.seconds,
                ))
            elif issue.kind == ISSUE_DUPLICATE:
                parts.append(tr("yt_chapter_duplicate", time=time, title=issue.title))
            else:
                parts.append(tr("yt_chapter_item", time=time, title=issue.title))
        if len(issues) > _MAX_LISTED_CHAPTERS:
            parts.append(tr("yt_chapters_more", count=len(issues) - _MAX_LISTED_CHAPTERS))
        return "; ".join(parts)

    def _set_state(self, state: str) -> None:
        self._state = state
        if state != "error":
            self._error_reason = ""
        self._render_state()

    def _render_state(self) -> None:
        """Placeholder and state row for the current ``_state`` — also
        called on a language change."""
        state = self._state
        self._placeholder.setVisible(state == "empty")
        if state in ("empty", "done"):
            self._state_bar.setVisible(False)
            return
        if state == "generating":
            text, link = tr("youtube_generating"), ""
            role = "dim"
        elif state == "error":
            text = f"{self._error_reason} {tr('youtube_retry_hint')}"
            link, role = tr("youtube_retry"), "warning-text"
        else:  # "ready"
            text, link = tr("youtube_not_generated"), tr("menu_run_youtube_package")
            role = "dim"
        set_role(self._state_label, role)
        self._state_label.setText(text)
        self._run_link.setText(link)
        self._run_link.setVisible(bool(link))
        self._state_bar.setVisible(True)

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
