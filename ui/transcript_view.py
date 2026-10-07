"""
Whispered UI - Transcript View Widget
Display, edit and search transcription results with timestamp and speaker support.

Reading mode lays the transcript out as paragraphs (domain/paragraphs.py)
in a proportional font at a comfortable measure, the way a document
reads — not one subtitle line per segment. Each segment keeps its own
character range in the document, so a click seeks to the segment under
the pointer and playback highlights the segment being spoken without
touching the text cursor or the user's selection.

Edit mode is unchanged: one "[HH:MM:SS.mmm] [Speaker] text" line per
segment in a monospace font, parsed back into segments on save.
"""

import bisect
import re
import time
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QLabel, QHBoxLayout,
    QPushButton, QLineEdit, QComboBox, QDialog, QFormLayout,
    QDialogButtonBox, QMenu, QApplication,
)
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QKeyEvent, QKeySequence, QShortcut, QTextBlockFormat,
    QTextCharFormat, QTextCursor, QTextDocument, QTextFrameFormat,
)

from domain.paragraphs import group_paragraphs
from transcriber import TranscriptionResult
from utils import format_duration, format_timestamp_vtt
from ui.icons import get_icon, IconColors
from ui.theme import SPEAKER_PALETTE, get_theme
from core.i18n import tr, tr_count
from ui.i18n_helpers import Retranslator

# Speaker color palette (keyed by original speaker id)
SPEAKER_COLORS = {
    f"Speaker {i + 1}": color for i, color in enumerate(SPEAKER_PALETTE)
}

# Pattern used when rendering for edit mode: "[HH:MM:SS.mmm] [SpeakerId] text"
_EDIT_LINE_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2}\.\d{3})\](?:\s*\[([^\]]*)\])?\s*(.*)"
)

# Reading measure: lines longer than this are hard to track back to their
# start; the text column centres in a wider viewport instead.
_READING_WIDTH = 760
_READING_FONT_PX = 15
_FIND_HIGHLIGHT_CAP = 2000
# Same amber as the player's bookmark markers (ui/player_widget.py).
_BOOKMARK_COLOR = "#f59e0b"


def _parse_vtt_to_seconds(ts: str) -> float:
    """Parse HH:MM:SS.mmm → float seconds."""
    try:
        h, m, rest = ts.split(":")
        s, ms = rest.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
    except Exception:
        return 0.0


@dataclass(frozen=True)
class _Span:
    """A clickable stretch of the reading document: a segment's text or a
    paragraph's timestamp. ``index`` is the segment index (-1 for a
    paragraph header)."""

    pos_from: int
    pos_to: int
    seconds: float
    end: float
    index: int


class _SpeakerRenameDialog(QDialog):
    """Simple dialog to rename / merge speakers.

    Each row is an editable QComboBox rather than a plain QLineEdit
    (B6, docs/IMPROVEMENT_PLAN_2026-08.ru.md): *suggestions* — names
    typed while renaming a speaker in any past record
    (core.history.HistoryStore.list_speaker_aliases()) — fill its
    dropdown as a reusable hint list. Diarization gives no cross-record
    speaker identity, so this is a suggestion list a user picks from,
    never an automatic guess: setEditText() below sets each field's
    starting text explicitly, so the top suggestion never silently
    becomes the value just because it's now offered.
    """

    def __init__(
        self, speaker_names: dict, parent=None, suggestions: "list[str] | None" = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("rename_speakers_title"))
        self.setMinimumWidth(340)
        self._combos: dict[str, QComboBox] = {}
        layout = QVBoxLayout(self)

        form = QFormLayout()
        for sid, display in sorted(speaker_names.items()):
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(suggestions or [])
            combo.setEditText(display)
            line_edit = combo.lineEdit()
            if line_edit is not None:
                line_edit.setPlaceholderText(sid)
            self._combos[sid] = combo
            form.addRow(f"{sid}:", combo)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_names(self) -> dict:
        return {
            sid: combo.currentText().strip() or sid for sid, combo in self._combos.items()
        }


class _FindKeys(QObject):
    """Shift+Enter = previous match, Escape = close, in the find field."""

    def __init__(self, view: "TranscriptView") -> None:
        super().__init__(view)
        self._view = view

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self._view._find_previous()
                else:
                    self._view._find_next()
                return True
            if key == Qt.Key.Key_Escape:
                self._view._close_find()
                return True
        return False


class TranscriptView(QWidget):
    """Widget to display, edit and search transcription results."""

    copy_requested = pyqtSignal()
    seek_requested = pyqtSignal(float)  # user clicked a segment → seek to its start time
    result_changed = pyqtSignal(str)  # text / speakers / structure
    # Context menu (R4): bookmark / start a chapter at a segment's start;
    # the second argument of chapter_requested is a suggested title.
    bookmark_requested = pyqtSignal(float)
    chapter_requested = pyqtSignal(float, str)
    # R5: cut (True) or keep (False) these segment indices in the edit.
    cut_requested = pyqtSignal(list, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result: Optional[TranscriptionResult] = None
        # Initial visibility comes from user settings.
        try:
            from config import get_config
            _cfg = get_config()
            self._show_timestamps = _cfg.show_timestamps
            self._show_speakers = _cfg.show_speaker_labels
        except Exception:
            self._show_timestamps = True
            self._show_speakers = True
        self._edit_mode = False
        # speaker_id → display name (e.g. {"Speaker 1": "Alice"})
        self._speaker_names: dict[str, str] = {}
        # Reading-document spans, sorted by position: every segment's text
        # and every paragraph timestamp (see _Span).
        self._spans: list[_Span] = []
        self._span_starts: list[int] = []
        # Segment spans by segment index, and their start times for bisect.
        self._segment_spans: list[_Span] = []
        self._segment_times: list[float] = []
        self._highlighted_index: int = -1
        self._find_matches: list[QTextCursor] = []
        self._find_current: int = -1
        # Times of the record's bookmarks; the segments holding them get
        # an amber underline.
        self._bookmark_times: list[float] = []
        # Segments cut from the edit (Cut tab), shown struck through.
        self._cut_indices: set[int] = set()
        # Follow playback: the highlighted segment is kept on screen until
        # the user scrolls away from it; "Back to playback" resumes.
        self._follow = True
        self._auto_scrolling = False
        # The player ticks its position ~5 times a second whether or not
        # it plays; "playing" is inferred from the position moving.
        self._last_tick_seconds: Optional[float] = None
        self._playing_until = 0.0
        self._press_pos = None
        self._header_compact = False
        self._i18n = Retranslator()
        self._setup_ui()
        self._i18n.call(self._retranslate_view)
        self._i18n.bind()

    def _retranslate_view(self) -> None:
        """Re-pull every caption after a live UI-language switch."""
        self.timestamps_btn.setText(tr("btn_timestamps"))
        self.timestamps_btn.setToolTip(tr("btn_timestamps"))
        self.speakers_btn.setText(tr("btn_speakers"))
        self.speakers_btn.setToolTip(tr("btn_speakers"))
        self.rename_btn.setText(tr("btn_rename_speakers"))
        self.edit_btn.setText(tr("btn_edit"))
        self.copy_btn.setText(tr("btn_copy"))
        self.copy_btn.setToolTip(tr("tooltip_copy"))
        self._button_labels = {
            btn: tr(key) for btn, key in self._button_label_keys.items()
        }
        if self._header_compact:
            for btn in self._icon_only_capable:
                btn.setText("")
        self.text_edit.setPlaceholderText(tr("transcript_placeholder"))
        self._find_edit.setPlaceholderText(tr("find_placeholder"))
        self._replace_edit.setPlaceholderText(tr("replace_placeholder"))
        for btn, key in self._find_button_keys.items():
            btn.setText(tr(key))
        self._find_prev_btn.setToolTip(tr("find_previous_tooltip"))
        self._find_next_btn.setToolTip(tr("find_next_tooltip"))
        self._close_find_btn.setToolTip(tr("find_close_tooltip"))
        self._follow_btn.setText(tr("transcript_follow_playback"))
        self._edit_hint.setText(tr("edit_hint"))
        self._render_stats()
        self._update_find_count()

    # ------------------------------------------------------------------ UI setup

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ── Header row ──────────────────────────────────────────
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        # Stats on the left of the toolbar: what used to be a separate
        # line under the text, which cost a row of reading space.
        self.stats_label = QLabel()
        self.stats_label.setProperty("role", "muted")
        self.stats_label.setProperty("size", "small")
        header_layout.addWidget(self.stats_label)
        header_layout.addStretch()

        # Timestamps toggle
        self.timestamps_btn = QPushButton(tr("btn_timestamps"))
        self.timestamps_btn.setIcon(get_icon('clock', IconColors.default(), 14))
        self.timestamps_btn.setToolTip(tr("btn_timestamps"))
        self.timestamps_btn.setCheckable(True)
        self.timestamps_btn.setChecked(self._show_timestamps)
        self.timestamps_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timestamps_btn.clicked.connect(self._toggle_timestamps)
        header_layout.addWidget(self.timestamps_btn)

        # Speakers toggle (green accent when checked)
        self.speakers_btn = QPushButton(tr("btn_speakers"))
        self.speakers_btn.setIcon(get_icon('user', IconColors.default(), 14))
        self.speakers_btn.setToolTip(tr("btn_speakers"))
        self.speakers_btn.setCheckable(True)
        self.speakers_btn.setChecked(self._show_speakers)
        self.speakers_btn.setProperty("role", "checkable-success")
        self.speakers_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speakers_btn.clicked.connect(self._toggle_speakers)
        self.speakers_btn.setVisible(False)
        header_layout.addWidget(self.speakers_btn)

        # Rename speakers button
        self.rename_btn = QPushButton(tr("btn_rename_speakers"))
        self.rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rename_btn.clicked.connect(self._rename_speakers)
        self.rename_btn.setVisible(False)
        header_layout.addWidget(self.rename_btn)

        # Edit toggle
        self.edit_btn = QPushButton(tr("btn_edit"))
        self.edit_btn.setCheckable(True)
        self.edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_btn.clicked.connect(self._toggle_edit)
        header_layout.addWidget(self.edit_btn)

        # Copy
        self.copy_btn = QPushButton(tr("btn_copy"))
        self.copy_btn.setIcon(get_icon('clipboard', IconColors.default(), 14))
        self.copy_btn.setToolTip(tr("tooltip_copy"))
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self.copy_requested.emit)
        header_layout.addWidget(self.copy_btn)

        # Icon-bearing buttons whose text can drop under _set_header_compact;
        # edit_btn/rename_btn have no icon of their own (ui/icons.py has none
        # to reuse) so they keep their short labels in both modes.
        self._icon_only_capable = (
            self.timestamps_btn, self.speakers_btn, self.copy_btn,
        )
        self._button_label_keys = {
            self.timestamps_btn: "btn_timestamps",
            self.speakers_btn: "btn_speakers",
            self.copy_btn: "btn_copy",
        }
        self._button_labels = {btn: btn.text() for btn in self._icon_only_capable}

        layout.addWidget(header)

        # ── Find/Replace bar (hidden by default) ─────────────────
        # Above the text, where the eye already is when Ctrl+F is pressed.
        self._find_bar = QWidget()
        find_layout = QHBoxLayout(self._find_bar)
        find_layout.setContentsMargins(0, 0, 0, 0)
        find_layout.setSpacing(6)

        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText(tr("find_placeholder"))
        self._find_edit.setClearButtonEnabled(True)
        self._find_edit.addAction(
            get_icon("search", IconColors.muted(), 14),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self._find_edit.textChanged.connect(self._on_find_text_changed)
        self._find_edit.installEventFilter(_FindKeys(self))
        find_layout.addWidget(self._find_edit, stretch=2)

        self._find_count = QLabel("")
        self._find_count.setProperty("role", "muted")
        self._find_count.setProperty("size", "small")
        find_layout.addWidget(self._find_count)

        self._find_prev_btn = QPushButton("↑")
        self._find_prev_btn.setProperty("role", "icon-button")
        self._find_prev_btn.clicked.connect(self._find_previous)
        find_layout.addWidget(self._find_prev_btn)
        self._find_next_btn = QPushButton("↓")
        self._find_next_btn.setProperty("role", "icon-button")
        self._find_next_btn.clicked.connect(self._find_next)
        find_layout.addWidget(self._find_next_btn)

        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText(tr("replace_placeholder"))
        find_layout.addWidget(self._replace_edit, stretch=1)

        replace_btn = QPushButton(tr("find_btn_replace"))
        replace_btn.clicked.connect(self._replace_one)
        find_layout.addWidget(replace_btn)

        replace_all_btn = QPushButton(tr("find_btn_all"))
        replace_all_btn.clicked.connect(self._replace_all)
        find_layout.addWidget(replace_all_btn)

        self._find_button_keys = {
            replace_btn: "find_btn_replace",
            replace_all_btn: "find_btn_all",
        }

        self._close_find_btn = QPushButton("×")
        self._close_find_btn.setProperty("role", "icon-button")
        self._close_find_btn.setFixedSize(24, 24)
        self._close_find_btn.clicked.connect(self._close_find)
        find_layout.addWidget(self._close_find_btn)

        self._find_bar.setVisible(False)
        layout.addWidget(self._find_bar)

        # ── Text area ────────────────────────────────────────────
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setProperty("role", "reading")
        self.text_edit.setPlaceholderText(tr("transcript_placeholder"))
        self.text_edit.viewport().installEventFilter(self)
        self.text_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text_edit.customContextMenuRequested.connect(self._show_context_menu)
        # MainWindow says whether a chapter can be added right now (there
        # is a YouTube package to add it to).
        self._can_add_chapter = lambda: False
        self.text_edit.verticalScrollBar().valueChanged.connect(self._on_user_scroll)
        layout.addWidget(self.text_edit, stretch=1)

        # Floating over the bottom of the text while the user has scrolled
        # away from the segment being played.
        self._follow_btn = QPushButton(tr("transcript_follow_playback"), self.text_edit)
        self._follow_btn.setProperty("role", "floating-pill")
        self._follow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._follow_btn.clicked.connect(self._resume_follow)
        self._follow_btn.setVisible(False)

        # ── Edit-mode hint ────────────────────────────────────────
        self.stats_bar = QWidget()
        self.stats_bar.setVisible(False)
        stats_layout = QHBoxLayout(self.stats_bar)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.addStretch()
        self._edit_hint = QLabel(tr("edit_hint"))
        self._edit_hint.setProperty("role", "warning-text")
        self._edit_hint.setProperty("size", "small")
        stats_layout.addWidget(self._edit_hint)
        layout.addWidget(self.stats_bar)

        # Keyboard shortcuts
        find_sc = QShortcut(QKeySequence("Ctrl+F"), self)
        find_sc.activated.connect(self._open_find)

        # Re-centring the reading column on resize re-lays out the whole
        # document; once per resize burst is enough.
        self._margin_timer = QTimer(self)
        self._margin_timer.setSingleShot(True)
        self._margin_timer.setInterval(60)
        self._margin_timer.timeout.connect(self._apply_reading_margins)

        self._set_buttons_enabled(False)

    # ------------------------------------------------------------------ public API

    def set_result(self, result: TranscriptionResult):
        self._result = result
        # Seed speaker names, honouring any names already on the result
        # (e.g. restored from history); default to identity mapping.
        existing = getattr(result, "speaker_names", None) or {}
        speakers = sorted({seg.speaker for seg in result.segments if seg.speaker})
        self._speaker_names = {s: existing.get(s, s) for s in speakers}
        result.speaker_names = dict(self._speaker_names)
        self._follow = True
        self._update_display()
        self._set_buttons_enabled(True)
        self._render_stats()

    def clear(self):
        self._result = None
        self._speaker_names = {}
        self.text_edit.clear()
        self._clear_spans()
        self._set_buttons_enabled(False)
        self.stats_label.setText("")
        self._follow_btn.setVisible(False)
        if self._edit_mode:
            self._exit_edit_mode(save=False)

    def get_text(self) -> str:
        return self.text_edit.toPlainText()

    def get_result(self) -> Optional[TranscriptionResult]:
        return self._result

    def showEvent(self, event):
        """Re-render on show.

        set_result() is often called while this widget's page is still
        hidden inside a QStackedWidget (e.g. RecordView isn't current yet),
        so text_edit lays out its document against stale construction-time
        geometry and only ~1/5 of the content appears reachable until some
        unrelated event (like a modal dialog) forces Qt to relayout. Redoing
        the render here, once the widget has its real on-screen geometry,
        fixes that without depending on caller ordering.
        """
        super().showEvent(event)
        if self._result and not self._edit_mode:
            self._update_display()
        self._update_header_compact()

    # Below this width the header row (stats + up to 5 buttons) no longer
    # fits and gets clipped by the splitter edge in RecordView — same
    # threshold class as FileSelector.set_compact()'s height check.
    _HEADER_COMPACT_WIDTH = 760

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_header_compact()
        self._place_follow_button()
        self._margin_timer.start()

    def _update_header_compact(self) -> None:
        compact = self.width() < self._HEADER_COMPACT_WIDTH
        if compact == self._header_compact:
            return
        self._header_compact = compact
        for btn in self._icon_only_capable:
            btn.setText("" if compact else self._button_labels[btn])

    def apply_display_settings(self):
        """Re-read show timestamps / speaker labels from config and refresh."""
        try:
            from config import get_config
            cfg = get_config()
        except Exception:
            return
        self._show_timestamps = cfg.show_timestamps
        self._show_speakers = cfg.show_speaker_labels
        self.timestamps_btn.setChecked(cfg.show_timestamps)
        self.speakers_btn.setChecked(cfg.show_speaker_labels)
        if self._result and not self._edit_mode:
            self._update_display()

    # ------------------------------------------------------------------ display

    def _render_stats(self) -> None:
        result = self._result
        if result is None:
            self.stats_label.setText("")
            return
        word_count = len(result.full_text.split())
        self.stats_label.setText(tr(
            "transcript_stats",
            words=tr_count("word_count", word_count),
            duration=format_duration(result.duration),
        ))

    def _set_buttons_enabled(self, enabled: bool):
        self.copy_btn.setEnabled(enabled)
        self.timestamps_btn.setEnabled(enabled)
        self.edit_btn.setEnabled(enabled)

    def _toggle_timestamps(self):
        self._show_timestamps = self.timestamps_btn.isChecked()
        from config import get_config, save_config
        get_config().show_timestamps = self._show_timestamps
        save_config()
        self._update_display()

    def _toggle_speakers(self):
        self._show_speakers = self.speakers_btn.isChecked()
        from config import get_config, save_config
        get_config().show_speaker_labels = self._show_speakers
        save_config()
        self._update_display()

    def _get_display_name(self, speaker_id: str) -> str:
        return self._speaker_names.get(speaker_id, speaker_id)

    def _get_speaker_color(self, speaker_id: str) -> str:
        return SPEAKER_COLORS.get(speaker_id, get_theme().text_secondary)

    def _update_display(self):
        if not self._result:
            return
        has_speakers = any(seg.speaker for seg in self._result.segments)
        self.speakers_btn.setVisible(has_speakers)
        self.rename_btn.setVisible(has_speakers)
        self._render_reading(with_speakers=has_speakers and self._show_speakers)

    def _clear_spans(self) -> None:
        self._spans = []
        self._span_starts = []
        self._segment_spans = []
        self._segment_times = []
        self._highlighted_index = -1
        self._find_matches = []
        self._find_current = -1

    def _reading_font(self) -> QFont:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        font.setPixelSize(_READING_FONT_PX)
        return font

    def _render_reading(self, with_speakers: bool) -> None:
        """Lay the transcript out as paragraphs and record every segment's
        character range (see module docstring)."""
        assert self._result is not None
        theme = get_theme()
        scroll = self.text_edit.verticalScrollBar().value()
        self.text_edit.setFont(self._reading_font())
        doc = QTextDocument(self.text_edit)
        doc.setDefaultFont(self._reading_font())
        doc.setDocumentMargin(4)
        cursor = QTextCursor(doc)

        body = QTextCharFormat()
        body.setForeground(QColor(theme.text_primary))
        stamp_fmt = QTextCharFormat()
        stamp_fmt.setForeground(QColor(theme.text_muted))
        stamp_fmt.setFontPointSize(10)
        para_fmt = QTextBlockFormat()
        para_fmt.setLineHeight(150.0, 1)  # QTextBlockFormat.LineHeightTypes.ProportionalHeight
        para_fmt.setBottomMargin(16)
        head_fmt = QTextBlockFormat()
        head_fmt.setTopMargin(6)
        head_fmt.setBottomMargin(2)

        self._clear_spans()
        spans: list[_Span] = []
        segment_spans: list[_Span] = []
        segments = self._result.segments
        first_block = True

        def new_block(fmt: QTextBlockFormat) -> None:
            nonlocal first_block
            if first_block:
                cursor.setBlockFormat(fmt)
                first_block = False
            else:
                cursor.insertBlock(fmt)

        for para in group_paragraphs(segments, by_speaker=with_speakers):
            speaker = para.speaker if with_speakers else None
            if speaker or self._show_timestamps:
                new_block(head_fmt)
                if speaker:
                    name_fmt = QTextCharFormat()
                    name_fmt.setForeground(QColor(self._get_speaker_color(speaker)))
                    name_fmt.setFontWeight(QFont.Weight.DemiBold)
                    name_fmt.setFontPointSize(11)
                    cursor.insertText(self._get_display_name(speaker), name_fmt)
                if self._show_timestamps:
                    if speaker:
                        cursor.insertText("   ", stamp_fmt)
                    pos = cursor.position()
                    cursor.insertText(format_duration(para.start), stamp_fmt)
                    spans.append(_Span(pos, cursor.position(), para.start, para.end, -1))
            new_block(para_fmt)
            for n, index in enumerate(para.indices):
                segment = segments[index]
                text = segment.text.strip()
                if n and text:
                    cursor.insertText(" ", body)
                pos = cursor.position()
                cursor.insertText(text, body)
                span = _Span(pos, cursor.position(), segment.start, segment.end, index)
                spans.append(span)
                segment_spans.append(span)

        self.text_edit.setDocument(doc)
        self._spans = sorted(spans, key=lambda s: s.pos_from)
        self._span_starts = [s.pos_from for s in self._spans]
        self._segment_spans = segment_spans
        self._segment_times = [s.seconds for s in segment_spans]
        self._apply_reading_margins()
        self.text_edit.verticalScrollBar().setValue(scroll)
        if self._find_bar.isVisible():
            self._run_find(keep_position=True)
        else:
            self._refresh_extra_selections()

    def _apply_reading_margins(self) -> None:
        """Centre the text in a column of at most _READING_WIDTH pixels."""
        if self._edit_mode:
            return
        doc = self.text_edit.document()
        width = self.text_edit.viewport().width()
        side = max(12, (width - _READING_WIDTH) // 2)
        root = doc.rootFrame()
        fmt: QTextFrameFormat = root.frameFormat()
        if int(fmt.leftMargin()) == side and int(fmt.rightMargin()) == side:
            return
        fmt.setLeftMargin(side)
        fmt.setRightMargin(side)
        fmt.setTopMargin(12)
        fmt.setBottomMargin(24)
        root.setFrameFormat(fmt)

    # ------------------------------------------------------------------ edit mode

    def _toggle_edit(self, checked: bool):
        if checked:
            self._enter_edit_mode()
        else:
            self._exit_edit_mode(save=True)

    def _enter_edit_mode(self):
        self._edit_mode = True
        self.edit_btn.setChecked(True)
        self.stats_bar.setVisible(True)
        self._follow_btn.setVisible(False)

        if not self._result:
            return

        # Render one line per segment in editable format
        lines = []
        for seg in self._result.segments:
            ts = format_timestamp_vtt(seg.start)
            if seg.speaker:
                lines.append(f"[{ts}] [{seg.speaker}] {seg.text.strip()}")
            else:
                lines.append(f"[{ts}] {seg.text.strip()}")

        self._clear_spans()
        self.text_edit.setExtraSelections([])
        doc = QTextDocument(self.text_edit)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(11)
        doc.setDefaultFont(mono)
        doc.setPlainText('\n'.join(lines))
        self.text_edit.setDocument(doc)
        self.text_edit.setReadOnly(False)

    def _exit_edit_mode(self, save: bool = True):
        self._edit_mode = False
        self.edit_btn.setChecked(False)
        self.stats_bar.setVisible(False)
        self.text_edit.setReadOnly(True)

        if save and self._result:
            changed = self._parse_edited_text()
            # Re-sync _speaker_names: keep existing display names where the
            # speaker id still exists; add identity entries for any new ids
            # the user may have typed in edit mode.
            existing = dict(self._speaker_names)
            new_ids = {seg.speaker for seg in self._result.segments if seg.speaker}
            self._speaker_names = {sid: existing.get(sid, sid) for sid in sorted(new_ids)}
            if self._result is not None:
                self._result.speaker_names = dict(self._speaker_names)
            if changed:
                self.result_changed.emit("structure")
                self._render_stats()

        self._update_display()

    def _parse_edited_text(self) -> bool:
        """Parse edited plaintext back into a fresh segment list.

        Rebuilding (rather than mutating in place) keeps the result consistent
        when the user adds, removes or splits lines: deleted lines drop their
        segments and split lines create new ones, with no stale leftovers.
        """
        from transcriber import Segment

        lines = self.text_edit.toPlainText().splitlines()
        # Collect (start, speaker, [text parts]) per timestamped line.
        parsed: list[list] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = _EDIT_LINE_RE.match(line)
            if m:
                parsed.append([
                    _parse_vtt_to_seconds(m.group(1)),
                    m.group(2) or None,
                    [m.group(3)],
                ])
            elif parsed:
                # continuation of the previous segment
                parsed[-1][2].append(line)
            # else: stray text before any timestamp → ignored

        if not parsed:
            return False

        if self._result is None:
            return False

        orig = self._result.segments
        new_segments: list = []
        for i, (start, speaker, text_parts) in enumerate(parsed):
            text = ' '.join(text_parts).strip()
            original = orig[i] if i < len(orig) else None
            # A pure text edit must not discard timing data collected by the
            # transcriber.  Structural edits take the deterministic branch
            # below and use explicit timestamps from the editor.
            if (original is not None and original.start == start
                    and original.speaker == speaker):
                new_segments.append(Segment(
                    start=original.start,
                    end=original.end,
                    text=text,
                    speaker=original.speaker,
                    words=list(original.words),
                ))
                continue
            # End time: next segment's start, else preserve original / duration.
            if i + 1 < len(parsed):
                end = max(parsed[i + 1][0], start)
            elif i < len(orig):
                end = max(orig[i].end, start)
            else:
                end = max(getattr(self._result, "duration", start), start)
            new_segments.append(Segment(
                start=start,
                end=end,
                text=text,
                speaker=speaker,
            ))

        # full_text is a property over segments, so export/AI/copy auto-update.
        self._result.segments = new_segments
        return True

    # ------------------------------------------------------------------ speakers

    def _rename_speakers(self):
        if not self._speaker_names:
            return
        suggestions: "list[str]" = []
        try:
            from core.history import get_history_store
            suggestions = get_history_store().list_speaker_aliases()
        except Exception:
            pass  # History unavailable/disabled — dialog still works, just with no hints.
        dlg = _SpeakerRenameDialog(self._speaker_names, self, suggestions=suggestions)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._speaker_names = dlg.get_names()
            # Propagate to the result so renames reach export / copy / history.
            if self._result is not None:
                self._result.speaker_names = dict(self._speaker_names)
            self._update_display()
            self.result_changed.emit("speakers")

    # ------------------------------------------------------------------ find & replace

    def _open_find(self):
        self._find_bar.setVisible(True)
        self._find_edit.setFocus()
        self._find_edit.selectAll()
        if self._find_edit.text():
            self._run_find(keep_position=True)

    def _close_find(self) -> None:
        self._find_bar.setVisible(False)
        self._find_matches = []
        self._find_current = -1
        self._refresh_extra_selections()
        self.text_edit.setFocus()

    def _on_find_text_changed(self, _text: str) -> None:
        self._run_find(keep_position=False)

    def _run_find(self, keep_position: bool) -> None:
        """Collect every match in the shown document and highlight them;
        the current match is the first at or after the text cursor."""
        query = self._find_edit.text()
        doc = self.text_edit.document()
        matches: list[QTextCursor] = []
        if query:
            cursor = QTextCursor(doc)
            while len(matches) < _FIND_HIGHLIGHT_CAP:
                cursor = doc.find(query, cursor)
                if cursor.isNull():
                    break
                matches.append(QTextCursor(cursor))
        self._find_matches = matches
        if not matches:
            self._find_current = -1
        else:
            anchor = self.text_edit.textCursor().selectionStart() if keep_position else 0
            starts = [m.selectionStart() for m in matches]
            self._find_current = min(bisect.bisect_left(starts, anchor), len(matches) - 1)
            self._show_current_match()
        self._update_find_count()
        self._refresh_extra_selections()

    def _show_current_match(self) -> None:
        if 0 <= self._find_current < len(self._find_matches):
            self.text_edit.setTextCursor(self._find_matches[self._find_current])
            self.text_edit.ensureCursorVisible()

    def _step_find(self, delta: int) -> None:
        if not self._find_matches:
            self._run_find(keep_position=True)
            if not self._find_matches:
                return
        else:
            self._find_current = (self._find_current + delta) % len(self._find_matches)
            self._show_current_match()
        self._update_find_count()
        self._refresh_extra_selections()

    def _find_next(self):
        self._step_find(1)

    def _find_previous(self) -> None:
        self._step_find(-1)

    def _replace_one(self):
        query = self._find_edit.text()
        replacement = self._replace_edit.text()
        if not query:
            return
        if self._edit_mode:
            cursor = self.text_edit.textCursor()
            if cursor.hasSelection() and cursor.selectedText() == query:
                cursor.insertText(replacement)
                if self._parse_edited_text():
                    self.result_changed.emit("text")
            self._run_find(keep_position=True)
            return
        if not self._result:
            return
        # The segment holding the current match, else the first that has one.
        target = None
        if 0 <= self._find_current < len(self._find_matches):
            span = self._span_at(self._find_matches[self._find_current].selectionStart())
            if span is not None and span.index >= 0:
                target = self._result.segments[span.index]
        if target is None or query not in target.text:
            target = next((s for s in self._result.segments if query in s.text), None)
        if target is not None:
            target.text = target.text.replace(query, replacement, 1)
            self._update_display()
            self.result_changed.emit("text")

    def _replace_all(self):
        query = self._find_edit.text()
        replacement = self._replace_edit.text()
        if not query:
            return
        if not self._result:
            return
        if self._edit_mode:
            text = self.text_edit.toPlainText()
            count = text.count(query)
            if count:
                self.text_edit.setPlainText(text.replace(query, replacement))
                if self._parse_edited_text():
                    self.result_changed.emit("text")
            self._find_count.setText(tr("find_replaced", count=count))
            return
        count = sum(segment.text.count(query) for segment in self._result.segments)
        if count == 0:
            self._find_count.setText(tr("find_no_matches"))
            return
        for segment in self._result.segments:
            segment.text = segment.text.replace(query, replacement)
        self._update_display()
        self.result_changed.emit("text")
        self._find_count.setText(tr("find_replaced", count=count))

    def _update_find_count(self) -> None:
        if not self._find_edit.text():
            self._find_count.setText("")
        elif not self._find_matches:
            self._find_count.setText(tr("find_no_matches"))
        else:
            self._find_count.setText(tr(
                "find_position", current=self._find_current + 1, total=len(self._find_matches)
            ))

    # ------------------------------------------------------------------ highlights

    def set_cut_indices(self, indices: set[int]) -> None:
        """Strike through the segments cut from the edit (R5)."""
        self._cut_indices = set(indices)
        self._refresh_extra_selections()

    def _indices_in_selection(self, fallback: Optional[_Span]) -> list[int]:
        """Segment indices the selection touches, else *fallback*'s."""
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            start, end = cursor.selectionStart(), cursor.selectionEnd()
            return [
                s.index for s in self._segment_spans
                if s.pos_to > start and s.pos_from < end
            ]
        return [fallback.index] if fallback is not None and fallback.index >= 0 else []

    def set_bookmarks(self, times: list[float]) -> None:
        """Underline the segments that hold a bookmark."""
        self._bookmark_times = sorted(times)
        self._refresh_extra_selections()

    def _bookmarked_spans(self) -> list[_Span]:
        spans: list[_Span] = []
        for seconds in self._bookmark_times:
            index = bisect.bisect_right(self._segment_times, seconds) - 1
            if 0 <= index < len(self._segment_spans):
                span = self._segment_spans[index]
                if not spans or spans[-1] is not span:
                    spans.append(span)
        return spans

    def _refresh_extra_selections(self) -> None:
        theme = get_theme()
        selections: list[QTextEdit.ExtraSelection] = []
        if not self._edit_mode and self._cut_indices:
            muted = QColor(theme.text_muted)
            for span in self._segment_spans:
                if span.index in self._cut_indices:
                    sel = QTextEdit.ExtraSelection()
                    cursor = QTextCursor(self.text_edit.document())
                    cursor.setPosition(span.pos_from)
                    cursor.setPosition(span.pos_to, QTextCursor.MoveMode.KeepAnchor)
                    sel.cursor = cursor
                    fmt = QTextCharFormat()
                    fmt.setFontStrikeOut(True)
                    fmt.setForeground(muted)
                    sel.format = fmt
                    selections.append(sel)
        if not self._edit_mode:
            for span in self._bookmarked_spans():
                sel = QTextEdit.ExtraSelection()
                cursor = QTextCursor(self.text_edit.document())
                cursor.setPosition(span.pos_from)
                cursor.setPosition(span.pos_to, QTextCursor.MoveMode.KeepAnchor)
                sel.cursor = cursor
                fmt = QTextCharFormat()
                fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
                fmt.setUnderlineColor(QColor(_BOOKMARK_COLOR))
                sel.format = fmt
                selections.append(sel)
        if 0 <= self._highlighted_index < len(self._segment_spans) and not self._edit_mode:
            span = self._segment_spans[self._highlighted_index]
            sel = QTextEdit.ExtraSelection()
            cursor = QTextCursor(self.text_edit.document())
            cursor.setPosition(span.pos_from)
            cursor.setPosition(span.pos_to, QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = cursor
            fmt = QTextCharFormat()
            fmt.setBackground(_qcolor_alpha(theme.accent, 0.22))
            sel.format = fmt
            selections.append(sel)
        for n, match in enumerate(self._find_matches):
            sel = QTextEdit.ExtraSelection()
            sel.cursor = match
            fmt = QTextCharFormat()
            current = n == self._find_current
            fmt.setBackground(_qcolor_alpha(theme.warning, 0.75 if current else 0.30))
            if current:
                fmt.setForeground(QColor("#1a1a1a"))
            sel.format = fmt
            selections.append(sel)
        self.text_edit.setExtraSelections(selections)

    # ------------------------------------------------------------------ player sync

    def highlight_at(self, seconds: float):
        """Highlight the segment covering *seconds* (called by player ticker)."""
        if self._edit_mode or not self._segment_spans or not self._result:
            return

        moved = self._last_tick_seconds is not None and abs(seconds - self._last_tick_seconds) > 0.01
        self._last_tick_seconds = seconds
        if moved:
            self._playing_until = time.monotonic() + 1.0
        elif not self._is_playing() and self._follow_btn.isVisible():
            self._follow_btn.setVisible(False)
        if not moved and self._highlighted_index < 0:
            # Nothing has played or been sought yet: a freshly opened
            # record isn't "at" its first sentence.
            return

        index = bisect.bisect_right(self._segment_times, seconds) - 1
        if index >= 0 and seconds >= self._segment_spans[index].end:
            # In a pause after this segment: keep it lit until the next
            # starts rather than flickering off between lines.
            if index + 1 < len(self._segment_spans):
                index = index if seconds - self._segment_spans[index].end < 1.5 else -1
        if index == self._highlighted_index:
            return
        self._highlighted_index = index
        self._refresh_extra_selections()
        if index >= 0 and moved:
            if self._follow:
                self._scroll_to_span(self._segment_spans[index])
            else:
                self._place_follow_button()
                self._follow_btn.setVisible(True)

    def _is_playing(self) -> bool:
        return time.monotonic() < self._playing_until

    def _scroll_to_span(self, span: _Span) -> None:
        """Bring *span* into the middle third of the viewport if it isn't
        already comfortably visible — a reader tracking the highlight sees
        the text move in calm steps, not a line at a time."""
        cursor = QTextCursor(self.text_edit.document())
        cursor.setPosition(span.pos_from)
        rect = self.text_edit.cursorRect(cursor)
        height = self.text_edit.viewport().height()
        if height / 6 <= rect.top() <= height * 2 / 3:
            return
        bar = self.text_edit.verticalScrollBar()
        self._auto_scrolling = True
        try:
            bar.setValue(bar.value() + rect.top() - height // 3)
        finally:
            self._auto_scrolling = False

    def _on_user_scroll(self, _value: int) -> None:
        if self._auto_scrolling or self._highlighted_index < 0 or not self._is_playing():
            return
        # The user moved the text while it plays: stop pulling it back to
        # the playhead until they ask for it.
        self._follow = False
        self._place_follow_button()
        self._follow_btn.setVisible(not self._edit_mode)

    def _resume_follow(self) -> None:
        self._follow = True
        self._follow_btn.setVisible(False)
        if 0 <= self._highlighted_index < len(self._segment_spans):
            self._scroll_to_span(self._segment_spans[self._highlighted_index])

    def _place_follow_button(self) -> None:
        btn = self._follow_btn
        btn.adjustSize()
        area = self.text_edit.rect()
        btn.move(
            (area.width() - btn.width()) // 2,
            area.height() - btn.height() - 14,
        )
        btn.raise_()

    def _span_at(self, position: int) -> Optional[_Span]:
        index = bisect.bisect_right(self._span_starts, position) - 1
        if index < 0:
            return None
        span = self._spans[index]
        return span if position <= span.pos_to else None

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """Click (press and release without dragging a selection) on a
        segment or paragraph time seeks the player there."""
        if obj is self.text_edit.viewport() and not self._edit_mode:
            etype = event.type()
            if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._press_pos = event.position().toPoint()
            elif etype == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                pressed, self._press_pos = self._press_pos, None
                if pressed is not None and (event.position().toPoint() - pressed).manhattanLength() < 4:
                    QTimer.singleShot(0, lambda pos=pressed: self._seek_at_point(pos))
        return super().eventFilter(obj, event)

    def set_chapter_check(self, can_add_chapter) -> None:
        """*can_add_chapter()* — is "Start a chapter here" available now."""
        self._can_add_chapter = can_add_chapter

    def _target_span(self, point) -> Optional[_Span]:
        """The segment a context menu is about: the start of the
        selection if there is one, else the segment under the pointer."""
        cursor = self.text_edit.textCursor()
        position = (
            cursor.selectionStart() if cursor.hasSelection()
            else self.text_edit.cursorForPosition(point).position()
        )
        span = self._span_at(position)
        if span is not None and span.index < 0:
            # A paragraph time stamp: the segment right after it.
            later = [s for s in self._segment_spans if s.pos_from >= span.pos_to]
            span = later[0] if later else None
        return span

    def _show_context_menu(self, point) -> None:
        if self._edit_mode:
            menu = self.text_edit.createStandardContextMenu()
            menu.exec(self.text_edit.viewport().mapToGlobal(point))
            return
        span = self._target_span(point)
        cursor = self.text_edit.textCursor()
        menu = QMenu(self)
        copy = menu.addAction(tr("btn_copy"))
        copy.setEnabled(cursor.hasSelection())
        copy.triggered.connect(self.text_edit.copy)
        copy_ts = menu.addAction(tr("transcript_copy_with_time"))
        copy_ts.setEnabled(span is not None)
        menu.addSeparator()
        play = menu.addAction(tr("transcript_play_from_here"))
        bookmark = menu.addAction(tr("transcript_bookmark_here"))
        chapter = menu.addAction(tr("transcript_chapter_here"))
        for action in (play, bookmark, chapter):
            action.setEnabled(span is not None)
        if span is not None and not self._can_add_chapter():
            chapter.setEnabled(False)
            chapter.setText(tr("transcript_chapter_here_unavailable"))
        menu.addSeparator()
        targets = self._indices_in_selection(span)
        all_cut = bool(targets) and all(i in self._cut_indices for i in targets)
        cut = menu.addAction(tr("transcript_keep_in_edit" if all_cut else "transcript_cut_from_edit"))
        cut.setEnabled(bool(targets))
        menu.addSeparator()
        select_all = menu.addAction(tr("transcript_select_all"))
        select_all.triggered.connect(self.text_edit.selectAll)
        chosen = menu.exec(self.text_edit.viewport().mapToGlobal(point))
        if chosen is cut and targets:
            self.cut_requested.emit(targets, not all_cut)
            return
        if span is None or chosen is None:
            return
        if chosen is copy_ts:
            self._copy_with_time(span)
        elif chosen is play:
            self._follow = True
            self.seek_requested.emit(span.seconds)
        elif chosen is bookmark:
            self.bookmark_requested.emit(span.seconds)
        elif chosen is chapter:
            self.chapter_requested.emit(span.seconds, self._chapter_suggestion(span))

    def _copy_with_time(self, span: _Span) -> None:
        """The selection (or the whole segment) prefixed with its time, the
        way a quote is cited: "[12:34] text"."""
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            text = cursor.selectedText().replace("\u2029", "\n").strip()
        elif self._result is not None and 0 <= span.index < len(self._result.segments):
            text = self._result.segments[span.index].text.strip()
        else:
            text = ""
        QApplication.clipboard().setText(f"[{format_duration(span.seconds)}] {text}")

    def _chapter_suggestion(self, span: _Span) -> str:
        """The segment's first words, as a starting point for a title."""
        if self._result is None or not 0 <= span.index < len(self._result.segments):
            return ""
        words = self._result.segments[span.index].text.strip().split()
        title = " ".join(words[:7]).rstrip(".,;:!?…")
        return title[:1].upper() + title[1:]

    def _seek_at_point(self, point) -> None:
        if self.text_edit.textCursor().hasSelection():
            return
        cursor = self.text_edit.cursorForPosition(point)
        span = self._span_at(cursor.position())
        if span is None:
            return
        self._follow = True
        self._follow_btn.setVisible(False)
        if span.index >= 0:
            self._highlighted_index = self._segment_spans.index(span) if span in self._segment_spans else -1
            self._refresh_extra_selections()
        self.seek_requested.emit(span.seconds)


def _qcolor_alpha(hex_color: str, alpha: float) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color
