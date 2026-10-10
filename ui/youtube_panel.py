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
    QRadioButton, QButtonGroup, QSpinBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor

from core.i18n import tr, tr_in
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
from application.youtube_publish import (
    DESCRIPTION_MAX_BYTES,
    TAGS_MAX_CHARS,
    TITLE_MAX_CHARS,
    description_bytes,
    fit_tags,
    normalize_titles,
    parse_tags,
    tags_length,
)
from core.youtube_description import (
    BLOCK_HASHTAGS,
    BLOCK_QUESTIONS,
    BLOCK_SIGNATURE,
    BLOCK_TEXT,
    BLOCK_TIMECODES,
    DEFAULT_DESCRIPTION_BLOCKS,
    DESCRIPTION_BLOCKS,
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
    above_the_fold,
    check_chapters,
    compose_description,
    format_chapter_lines,
    normalize_hashtags,
    format_youtube_description,
    format_youtube_timestamp,
    parse_chapter_lines,
    shift_chapters,
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

# Largest shift between recording and video the offset field accepts.
_MAX_OFFSET_SECONDS = 3600


def _signed_seconds(seconds: int) -> str:
    """``+0:15`` / ``−1:05`` for the offset hint."""
    sign = "+" if seconds >= 0 else "−"
    return sign + format_youtube_timestamp(abs(seconds))


# Roughly how much of a title search results and phones show; past this a
# title still fits YouTube's limit but gets cut off for most viewers.
_TITLE_VISIBLE_CHARS = 70

# Copy button caption per section, in _TAB_SPECS order.
_COPY_KEYS = (
    "yt_copy_timecodes", "yt_copy_title", "yt_copy_description", "yt_copy_tags", "yt_copy_questions",
)


class _TitleLabel(QLabel):
    """A wrapped title next to its radio button; clicking it picks the
    title, as a radio button's own caption would (QRadioButton cannot wrap)."""

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):  # noqa: N802 — Qt override
        self.clicked.emit()
        super().mouseReleaseEvent(event)


# The description's block chips: block id → caption key.
_DESC_BLOCK_LABELS = (
    (BLOCK_TEXT, "yt_desc_block_text"),
    (BLOCK_TIMECODES, "yt_desc_block_timecodes"),
    (BLOCK_QUESTIONS, "yt_desc_block_questions"),
    (BLOCK_SIGNATURE, "yt_desc_block_signature"),
    (BLOCK_HASHTAGS, "yt_desc_block_hashtags"),
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

    # Created by _setup_ui() from _TAB_SPECS (setattr by name).
    _chapters_edit: QPlainTextEdit
    _titles_edit: QPlainTextEdit
    _desc_edit: QPlainTextEdit
    _tags_edit: QPlainTextEdit
    _questions_edit: QPlainTextEdit


    # generate_requested: the run link was clicked — MainWindow runs the
    # same single-step job as the menu action. generation_finished: emitted
    # by set_result()/set_error() once MainWindow's "youtube_package"
    # JobRunner has settled.
    generate_requested = pyqtSignal()
    generation_finished = pyqtSignal(bool)
    # A chapter's time was clicked: seconds to move the player to.
    seek_requested = pyqtSignal(int)
    # The record's chapters (record_chapters()) may have changed: a new
    # package, an edit, back to the model's, or none any more.
    chapters_changed = pyqtSignal()
    # Whether the panel has anything to show changed (see has_content()).
    content_changed = pyqtSignal()
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
        # The package's hashtags (yt_hashtags), shown after the signature.
        self._hashtags: list = []
        self._titles: list[str] = []
        self._title_labels: list[tuple[str, QLabel]] = []
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
        self._offset_label.setText(tr("yt_offset_label"))
        self._offset_hint.setText(tr("yt_offset_hint"))
        self._offset_spin.setSuffix(tr("yt_offset_suffix"))
        self._offset_spin.setToolTip(tr("yt_offset_hint"))
        self._offset_spin.setAccessibleName(tr("yt_offset_label"))
        self._desc_blocks_label.setText(tr("yt_desc_blocks"))
        for block, key in _DESC_BLOCK_LABELS:
            self._desc_block_btns[block].setText(tr(key))
        self._render_description_meta()
        self._render_copy_caption()
        self._render_tags_meta()
        self._render_titles()
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

        self._copy_btn = QPushButton(tr(_COPY_KEYS[0]))
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
            elif spec.insight_type == "yt_description":
                self._tabs.addItem(self._build_description_section(edit), tr(spec.label_key))
            elif spec.insight_type == "yt_titles":
                self._tabs.addItem(self._build_titles_section(edit), tr(spec.label_key))
            elif spec.insight_type == "yt_tags":
                self._tabs.addItem(self._build_tags_section(edit), tr(spec.label_key))
            else:
                self._tabs.addItem(edit, tr(spec.label_key))

        layout.addWidget(self._tabs, stretch=1)
        self._tabs.currentChanged.connect(self._render_copy_caption)
        # Takes the slack only while the sections are hidden, so the state
        # row sits under the buttons instead of floating mid-panel; once the
        # sections show, their stretch factor wins.
        layout.addStretch()

    def _build_titles_section(self, edit: QPlainTextEdit) -> QWidget:
        """One radio row per title candidate with its length against
        YouTube's limit; the chosen one is what Copy and the publish dialog
        take first. The numbered text form stays (hidden) for Save, and is
        shown instead when the step gave no list."""
        self._title_group = QButtonGroup(self)
        self._title_group.setExclusive(True)
        self._title_rows = QWidget()
        self._title_rows_layout = QVBoxLayout(self._title_rows)
        self._title_rows_layout.setContentsMargins(4, 4, 4, 4)
        self._title_rows_layout.setSpacing(4)
        self._title_hint = QLabel(tr("yt_title_hint", limit=TITLE_MAX_CHARS, visible=_TITLE_VISIBLE_CHARS))
        self._title_hint.setWordWrap(True)
        self._title_hint.setProperty("role", "muted")
        self._title_hint.setStyleSheet("font-size: 11px;")
        self._title_hint.setVisible(False)

        self._title_scroll = QScrollArea()
        self._title_scroll.setWidgetResizable(True)
        self._title_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._title_scroll.setWidget(self._title_rows)
        self._title_scroll.setVisible(False)

        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self._title_hint)
        box.addWidget(self._title_scroll)
        box.addWidget(edit)
        return section

    def _build_tags_section(self, edit: QPlainTextEdit) -> QWidget:
        """Tags text with its length against YouTube's 500-character budget
        (counted the way the upload counts it)."""
        self._tags_size = QLabel()
        self._tags_size.setWordWrap(True)
        self._tags_size.setProperty("role", "muted")
        self._tags_size.setStyleSheet("font-size: 11px;")
        self._tags_size.setVisible(False)
        edit.textChanged.connect(self._render_tags_meta)
        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(edit)
        box.addWidget(self._tags_size)
        return section

    # ── Title and tags ──────────────────────────────────────────────

    def _chosen_title(self) -> str:
        chosen = self._overlay.get("title")
        if isinstance(chosen, str) and chosen in self._titles:
            return chosen
        return self._titles[0] if self._titles else ""

    def _on_title_picked(self, title: str) -> None:
        if title == self._chosen_title():
            return
        model_first = self._titles[0] if self._titles else ""
        self._store_overlay(set_edit(self._overlay, "title", title, model_first))
        self._mark_chosen_title()

    def _mark_chosen_title(self) -> None:
        """Bold the chosen title — the radio dot alone is faint in the
        dark theme, and weight is a signal that does not rely on colour."""
        chosen = self._chosen_title()
        for title, label in self._title_labels:
            label.setStyleSheet("font-weight: 600;" if title == chosen else "")

    def _render_titles(self) -> None:
        """Rebuild the title rows for ``_titles`` — also on a language change."""
        layout = self._title_rows_layout
        for button in self._title_group.buttons():
            self._title_group.removeButton(button)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        chosen = self._chosen_title()
        self._title_labels = []
        for title in self._titles:
            row = QWidget(self._title_rows)
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)
            radio = QRadioButton(row)
            radio.setAccessibleName(title)
            radio.setChecked(title == chosen)
            radio.toggled.connect(
                lambda checked, t=title: self._on_title_picked(t) if checked else None
            )
            self._title_group.addButton(radio)
            line.addWidget(radio, alignment=Qt.AlignmentFlag.AlignTop)
            label = _TitleLabel(title, row)
            label.setWordWrap(True)
            label.clicked.connect(radio.click)
            self._title_labels.append((title, label))
            line.addWidget(label, stretch=1)
            count = QLabel(self._title_count_text(title), row)
            count.setStyleSheet("font-size: 11px;")
            length = len(title)
            set_role(count, "danger-text" if length > TITLE_MAX_CHARS
                     else "warning-text" if length > _TITLE_VISIBLE_CHARS else "muted")
            line.addWidget(count, alignment=Qt.AlignmentFlag.AlignTop)
            layout.addWidget(row)
        layout.addStretch()
        self._mark_chosen_title()
        has_rows = bool(self._titles)
        self._title_scroll.setVisible(has_rows)
        self._title_hint.setVisible(has_rows)
        self._title_hint.setText(tr("yt_title_hint", limit=TITLE_MAX_CHARS, visible=_TITLE_VISIBLE_CHARS))
        self._titles_edit.setVisible(not has_rows)

    @staticmethod
    def _title_count_text(title: str) -> str:
        length = len(title)
        if length > TITLE_MAX_CHARS:
            return tr("yt_title_count_over", count=length, limit=TITLE_MAX_CHARS)
        if length > _TITLE_VISIBLE_CHARS:
            return tr("yt_title_count_cut", count=length, limit=TITLE_MAX_CHARS)
        return tr("yt_title_count", count=length, limit=TITLE_MAX_CHARS)

    def _render_tags_meta(self) -> None:
        tags = parse_tags(self._tags_edit.toPlainText())
        if not tags:
            self._tags_size.setVisible(False)
            return
        _, dropped = fit_tags(tags)
        size = tags_length(tags)
        if dropped:
            text = tr("yt_tags_size_over", count=size, limit=TAGS_MAX_CHARS, dropped=len(dropped))
            role = "warning-text"
        else:
            text, role = tr("yt_tags_size", count=size, limit=TAGS_MAX_CHARS), "muted"
        set_role(self._tags_size, role)
        self._tags_size.setText(text)
        self._tags_size.setVisible(True)

    def _render_copy_caption(self, *_args) -> None:
        index = self._tabs.currentIndex()
        key = _COPY_KEYS[index] if 0 <= index < len(_COPY_KEYS) else _COPY_KEYS[0]
        self._copy_btn.setText(tr(key))

    def _build_description_section(self, edit: QPlainTextEdit) -> QWidget:
        """Block chips (what goes into the description), the assembled
        description, its size against YouTube's limit and the part shown
        before "...more"."""
        chips = QHBoxLayout()
        chips.setContentsMargins(4, 4, 4, 0)
        chips.setSpacing(4)
        self._desc_blocks_label = QLabel(tr("yt_desc_blocks"))
        self._desc_blocks_label.setProperty("role", "muted")
        self._desc_blocks_label.setStyleSheet("font-size: 11px;")
        chips.addWidget(self._desc_blocks_label)
        self._desc_block_btns: dict[str, QPushButton] = {}
        for block, key in _DESC_BLOCK_LABELS:
            button = QPushButton(tr(key))
            button.setCheckable(True)
            button.setProperty("role", "quick-chip")
            button.toggled.connect(
                lambda checked, b=block: self._on_desc_block_toggled(b, checked)
            )
            chips.addWidget(button)
            self._desc_block_btns[block] = button
        chips.addStretch()

        meta = QHBoxLayout()
        meta.setContentsMargins(4, 0, 4, 0)
        meta.setSpacing(8)
        self._desc_fold = QLabel()
        self._desc_fold.setWordWrap(True)
        self._desc_fold.setProperty("role", "muted")
        self._desc_fold.setStyleSheet("font-size: 11px;")
        self._desc_fold.setVisible(False)
        meta.addWidget(self._desc_fold, stretch=1)
        self._desc_size = QLabel()
        self._desc_size.setWordWrap(True)
        self._desc_size.setProperty("role", "muted")
        self._desc_size.setStyleSheet("font-size: 11px;")
        meta.addWidget(self._desc_size)

        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.addLayout(chips)
        box.addLayout(meta)
        box.addWidget(edit)
        return section

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

        # Offset between the recording and the published video (an intro
        # the recording lacks): applied to the timecodes, not to editing.
        self._offset_label = QLabel(tr("yt_offset_label"))
        self._offset_label.setProperty("role", "muted")
        self._offset_label.setStyleSheet("font-size: 11px;")
        self._offset_spin = QSpinBox()
        self._offset_spin.setRange(-_MAX_OFFSET_SECONDS, _MAX_OFFSET_SECONDS)
        self._offset_spin.setSuffix(tr("yt_offset_suffix"))
        self._offset_spin.setToolTip(tr("yt_offset_hint"))
        self._offset_spin.setAccessibleName(tr("yt_offset_label"))
        self._offset_label.setBuddy(self._offset_spin)
        self._offset_spin.valueChanged.connect(self._on_offset_changed)
        self._offset_hint = QLabel(tr("yt_offset_hint"))
        self._offset_hint.setWordWrap(True)
        self._offset_hint.setProperty("role", "muted")
        self._offset_hint.setStyleSheet("font-size: 11px;")
        offset_row = QHBoxLayout()
        offset_row.setContentsMargins(4, 0, 4, 0)
        offset_row.setSpacing(6)
        offset_row.addWidget(self._offset_label)
        offset_row.addWidget(self._offset_spin)
        offset_row.addWidget(self._offset_hint, stretch=1)
        self._offset_bar = QWidget()
        self._offset_bar.setLayout(offset_row)
        self._offset_bar.setVisible(False)

        section = QWidget()
        box = QVBoxLayout(section)
        box.setContentsMargins(0, 0, 0, 0)
        box.addLayout(bar)
        box.addWidget(self._offset_bar)
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
        offset = self._offset()
        for start, title in chapters:
            # Shown in video time (what YouTube lists); seeks the player in
            # recording time.
            row = ChapterRow(
                max(0, start - offset), title, self._chapter_rows,
                label=format_youtube_timestamp(start),
            )
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
        self._questions = []
        self._hashtags = []
        self._titles = []
        self._reset_chapter_edits()
        self._render_titles()
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
        self._questions = []
        self._hashtags = []
        self._titles = []
        self._reset_chapter_edits()
        self._render_titles()
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
        self._overlay = load_overlay(self._overlay_file())
        if isinstance(data, list):
            self._model_chapters = data
            self._apply_chapters()
        else:
            self._model_chapters = None
            self._chapters_data = None
            self._chapters_edit.setPlainText(str(data) if data else tr("youtube_empty"))
            self._set_chapter_check(None)
        self._render_chapter_editing()

        titles = payload.get("yt_titles")
        if isinstance(titles, list):
            self._titles_edit.setPlainText(
                "\n\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
            )
            # The same cleaning the publish dialog applies, so the choice
            # made here is one of the dialog's candidates.
            self._titles = normalize_titles(titles)
        else:
            self._titles_edit.setPlainText(str(titles) if titles else "")
            self._titles = []
        self._render_titles()

        desc = payload.get("yt_description")
        if isinstance(desc, list) and desc:
            self._description_text = desc[0] if isinstance(desc[0], str) else str(desc[0])
        elif isinstance(desc, str):
            self._description_text = desc

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

        hashtags = payload.get("yt_hashtags")
        self._hashtags = normalize_hashtags(hashtags) if hashtags else []

        # Last, once text, chapters and questions are all in.
        self._compose_description()
        # A package restored from disk arrives after clear() hid the
        # sections, without begin_generating() to show them again.
        self._tabs.setVisible(True)
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
            "titles": self._publish_titles(),
            "description": self._desc_edit.toPlainText(),
            "tags": self._tags_edit.toPlainText(),
            "language": self._publish_language(),
            "chapter_check": self._chapter_check,
        }

    def _publish_titles(self) -> list[str]:
        """Title candidates, the one chosen in the Titles section first —
        the publish dialog preselects the first. Without a parsed list,
        the raw lines of the Titles text."""
        if not self._titles:
            return self._titles_edit.toPlainText().splitlines()
        chosen = self._chosen_title()
        return [chosen] + [title for title in self._titles if title != chosen]

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

    # ── Description ─────────────────────────────────────────────────

    def refresh_description(self) -> None:
        """Re-assemble the description — e.g. after Settings changed the
        channel signature. A no-op until a package is shown."""
        if self._state == "done":
            self._compose_description()

    def _desc_blocks(self) -> list[str]:
        chosen = self._overlay.get("description_blocks")
        if isinstance(chosen, list):
            return [block for block in DESCRIPTION_BLOCKS if block in chosen]
        return list(DEFAULT_DESCRIPTION_BLOCKS)

    def _compose_description(self) -> None:
        """Assemble the Description tab — one ready-to-paste YouTube
        description — from the chosen blocks: the model's text, the
        (possibly edited) chapter timecodes, key questions, and the channel
        signature from Settings. Re-run whenever any of them changes."""
        from config import get_config

        text = compose_description(
            blocks=self._desc_blocks(),
            text=self._description_text,
            chapters=self._chapters_data,
            questions=shift_chapters(self._questions, self._offset()),
            signature=get_config().yt_channel_signature,
            hashtags=self._hashtags,
            # Labels in the package's language, not the UI's: an English
            # description must not say "Тайм-коды:".
            timecodes_label=tr_in(self._publish_language(), "youtube_timecodes_label"),
            questions_label=tr_in(self._publish_language(), "yt_desc_questions_label"),
        )
        self._desc_edit.setPlainText(text)
        self._render_description_meta()

    def _on_desc_block_toggled(self, block: str, checked: bool) -> None:
        chosen = set(self._desc_blocks())
        if checked:
            chosen.add(block)
        else:
            chosen.discard(block)
        ordered = [b for b in DESCRIPTION_BLOCKS if b in chosen]
        self._store_overlay(set_edit(
            self._overlay, "description_blocks", ordered, list(DEFAULT_DESCRIPTION_BLOCKS),
        ))
        self._compose_description()

    def _render_description_meta(self) -> None:
        """Block chips, size against YouTube's limit, and the part shown
        before "...more" — also called on a language change."""
        from config import get_config

        chosen = set(self._desc_blocks())
        for block, button in self._desc_block_btns.items():
            blocked = button.blockSignals(True)
            button.setChecked(block in chosen)
            button.blockSignals(blocked)
        has_signature = bool(get_config().yt_channel_signature.strip())
        signature_btn = self._desc_block_btns[BLOCK_SIGNATURE]
        signature_btn.setEnabled(has_signature)
        signature_btn.setToolTip("" if has_signature else tr("yt_desc_signature_empty"))

        text = self._desc_edit.toPlainText()
        size = description_bytes(text)
        over = size > DESCRIPTION_MAX_BYTES
        self._desc_size.setText(tr(
            "yt_desc_size_over" if over else "yt_desc_size",
            count=size, limit=DESCRIPTION_MAX_BYTES,
        ))
        set_role(self._desc_size, "warning-text" if over else "muted")
        self._desc_fold.setText(tr("yt_desc_fold", text=above_the_fold(text)) if text else "")
        self._desc_fold.setVisible(bool(text))

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

    def record_chapters(self) -> list[dict]:
        """This record's chapters as the user has them — the edit if there
        is one, else the package's — in recording time (no video offset),
        sorted, valid items only. Empty when no package is shown. What the
        Insights tab and the player's timeline show (Y6)."""
        if self._model_chapters is None:
            return []
        chapters, _ = parse_chapter_lines(format_chapter_lines(self._effective_chapters()))
        return chapters

    def _offset(self) -> int:
        """Seconds the published video runs ahead of the recording."""
        value = self._overlay.get("offset", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def _on_offset_changed(self, value: int) -> None:
        if value == self._offset() or self._model_chapters is None:
            return
        self._store_overlay(set_edit(self._overlay, "offset", value, 0))
        self._apply_chapters()
        self._compose_description()
        self._render_chapter_editing()

    def _effective_chapters(self) -> list:
        edited = self._overlay.get("chapters")
        if isinstance(edited, list):
            return edited
        return self._model_chapters or []

    def _apply_chapters(self) -> None:
        """Show the effective chapters everywhere: rows, Copy/Save text,
        checks, and (via _compose_description) the description."""
        offset = self._offset()
        # From here on, chapters are in video time — what YouTube will list.
        chapters = shift_chapters(self._effective_chapters(), offset)
        self._chapters_data = chapters
        self._chapters_edit.setReadOnly(True)
        text = format_youtube_description(chapters)
        self._chapters_edit.setPlainText(text or tr("youtube_empty"))
        # Plain-dict segments (some tests) carry no usable duration;
        # the past-end check is then simply skipped.
        duration = getattr(self._segments[-1], "end", None) if self._segments else None
        if duration is not None:
            duration = max(0.0, duration + offset)
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
        self._compose_description()
        self._render_chapter_editing()

    def can_add_chapter(self) -> bool:
        """Whether a chapter can be added from elsewhere (the transcript's
        context menu): there is a package and its chapters aren't open in
        the editor right now."""
        return self._model_chapters is not None and not self._editing_chapters

    def add_chapter(self, seconds: float, title: str) -> bool:
        """Add (or retitle) the chapter starting at *seconds* — recording
        time — as a user edit (Y3 overlay), exactly as if typed into the
        chapter editor. Returns False when there is no package to edit."""
        if not self.can_add_chapter() or not title.strip():
            return False
        start = max(0, int(seconds))
        chapters = [
            dict(item) for item in self._effective_chapters()
            if isinstance(item, dict) and int(item.get("start", -1)) != start
        ]
        chapters.append({"start": start, "title": title.strip()})
        chapters.sort(key=lambda item: item["start"])
        normal, _ = parse_chapter_lines(format_chapter_lines(chapters))
        model = self._model_chapters or []
        model_normal, _ = parse_chapter_lines(format_chapter_lines(model))
        if normal == model_normal:
            overlay = drop_edit(self._overlay, "chapters")
        else:
            overlay = set_edit(self._overlay, "chapters", normal, model)
        self._store_overlay(overlay)
        self._apply_chapters()
        self._compose_description()
        self._render_chapter_editing()
        return True

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
        self._compose_description()
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
        self._offset_bar.setVisible(has_model and not editing)
        blocked = self._offset_spin.blockSignals(True)
        self._offset_spin.setValue(self._offset())
        self._offset_spin.blockSignals(blocked)
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
        elif editing and self._offset():
            text = tr("yt_chapters_edit_hint_offset", offset=_signed_seconds(self._offset()))
            role = "muted"
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
        self.chapters_changed.emit()

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
        # A first chapter at 0:00 in the recording lands on the offset in
        # the video; pulling it back to 0:00 just folds the intro into it —
        # expected, not worth a warning.
        moved = tuple(m for m in check.issues_of(ISSUE_FIRST_MOVED) if m.seconds != self._offset())
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
        changed = state != self._state
        self._state = state
        if state != "error":
            self._error_reason = ""
        self._render_state()
        if changed:
            self.content_changed.emit()

    def has_content(self) -> bool:
        """A package, one being made, or a failed attempt to report."""
        return self._state in ("generating", "error", "done")

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
        """Copy what the open section is for: the timecodes, the chosen
        title, the assembled description, the tags, or the questions."""
        index = self._tabs.currentIndex()
        is_titles = 0 <= index < len(_TAB_SPECS) and _TAB_SPECS[index].insight_type == "yt_titles"
        if is_titles and self._titles:
            text = self._chosen_title()
        else:
            text = self._edit_for_index(index).toPlainText()
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
        the recipe run's auto-save step;
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
        docs/archive/AUDIT_EXECUTION_PLAN_2026-08.ru.md, R5-full step 3) — same
        mechanism already used for Cover and article exports. Best-effort:
        the .txt file is already safely on disk by the time this runs, so
        a manifest failure must not turn a successful save into an error.
        """
        from application.artifacts import record_export

        record_export(
            record_id=self._record_id,
            source_path=self._source_path,
            segments=self._segments,
            language=self._transcript_language,
            type=f"youtube_{file_key}",
            path=path,
        )
