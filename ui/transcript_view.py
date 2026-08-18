"""
Whispered UI - Transcript View Widget
Display, edit and search transcription results with timestamp and speaker support.
"""

import html
import re
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QLabel, QHBoxLayout,
    QPushButton, QLineEdit, QDialog, QFormLayout,
    QDialogButtonBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QFont, QTextCharFormat, QColor, QTextCursor, QKeySequence, QShortcut
)

from transcriber import TranscriptionResult
from utils import format_timestamp_vtt
from ui.icons import get_icon, IconColors
from ui.theme import SPEAKER_PALETTE, get_theme
from core.i18n import tr

# Speaker color palette (keyed by original speaker id)
SPEAKER_COLORS = {
    f"Speaker {i + 1}": color for i, color in enumerate(SPEAKER_PALETTE)
}

# Pattern used when rendering for edit mode: "[HH:MM:SS.mmm] [SpeakerId] text"
_EDIT_LINE_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2}\.\d{3})\](?:\s*\[([^\]]*)\])?\s*(.*)"
)


def _parse_vtt_to_seconds(ts: str) -> float:
    """Parse HH:MM:SS.mmm → float seconds."""
    try:
        h, m, rest = ts.split(":")
        s, ms = rest.split(".")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
    except Exception:
        return 0.0


class _SpeakerRenameDialog(QDialog):
    """Simple dialog to rename / merge speakers."""

    def __init__(self, speaker_names: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("rename_speakers_title"))
        self.setMinimumWidth(340)
        self._edits: dict[str, QLineEdit] = {}
        layout = QVBoxLayout(self)

        form = QFormLayout()
        for sid, display in sorted(speaker_names.items()):
            edit = QLineEdit(display)
            edit.setPlaceholderText(sid)
            self._edits[sid] = edit
            form.addRow(f"{sid}:", edit)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_names(self) -> dict:
        return {sid: edit.text().strip() or sid for sid, edit in self._edits.items()}


class TranscriptView(QWidget):
    """Widget to display, edit and search transcription results."""

    copy_requested = pyqtSignal()
    export_requested = pyqtSignal()
    seek_requested = pyqtSignal(float)  # user clicked a segment → seek to its start time
    result_changed = pyqtSignal(str)  # text / speakers / structure

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
        # segment positions for player sync: (start_sec, end_sec, block_pos)
        self._segment_positions: list[tuple[float, float, int]] = []
        self._highlighted_block: int = -1
        self._header_compact = False
        self._setup_ui()

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

        # No title label here — the enclosing QTabWidget's own tab label
        # ("Транскрипт") already names this view directly above it;
        # repeating it here just duplicated the same word twice.
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

        # Export
        self.export_btn = QPushButton(tr("btn_export"))
        self.export_btn.setIcon(get_icon('save', IconColors.WHITE, 14))
        self.export_btn.setProperty("variant", "primary")
        self.export_btn.setToolTip(tr("tooltip_export"))
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.clicked.connect(self.export_requested.emit)
        header_layout.addWidget(self.export_btn)

        # Icon-bearing buttons whose text can drop under _set_header_compact;
        # edit_btn/rename_btn have no icon of their own (ui/icons.py has none
        # to reuse) so they keep their short labels in both modes.
        self._icon_only_capable = (
            self.timestamps_btn, self.speakers_btn, self.copy_btn, self.export_btn,
        )
        self._button_labels = {btn: btn.text() for btn in self._icon_only_capable}

        layout.addWidget(header)

        # ── Text area ────────────────────────────────────────────
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Monospace", 11))
        self.text_edit.setPlaceholderText(tr("transcript_placeholder"))
        self.text_edit.mousePressEvent = self._on_click
        layout.addWidget(self.text_edit, stretch=1)

        # ── Find/Replace bar (hidden by default) ─────────────────
        self._find_bar = QWidget()
        find_layout = QHBoxLayout(self._find_bar)
        find_layout.setContentsMargins(0, 0, 0, 0)
        find_layout.setSpacing(6)

        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText(tr("find_placeholder"))
        self._find_edit.returnPressed.connect(self._find_next)
        find_layout.addWidget(self._find_edit, stretch=1)

        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText(tr("replace_placeholder"))
        find_layout.addWidget(self._replace_edit, stretch=1)

        find_next_btn = QPushButton(tr("find_btn_next"))
        find_next_btn.clicked.connect(self._find_next)
        find_layout.addWidget(find_next_btn)

        replace_btn = QPushButton(tr("find_btn_replace"))
        replace_btn.clicked.connect(self._replace_one)
        find_layout.addWidget(replace_btn)

        replace_all_btn = QPushButton(tr("find_btn_all"))
        replace_all_btn.clicked.connect(self._replace_all)
        find_layout.addWidget(replace_all_btn)

        self._find_count = QLabel("")
        self._find_count.setProperty("role", "muted")
        self._find_count.setProperty("size", "small")
        find_layout.addWidget(self._find_count)

        close_find_btn = QPushButton("×")
        close_find_btn.setFixedSize(22, 22)
        close_find_btn.clicked.connect(lambda: self._find_bar.setVisible(False))
        find_layout.addWidget(close_find_btn)

        self._find_bar.setVisible(False)
        layout.addWidget(self._find_bar)

        # ── Stats bar ─────────────────────────────────────────────
        self.stats_bar = QWidget()
        self.stats_bar.setVisible(False)
        stats_layout = QHBoxLayout(self.stats_bar)
        stats_layout.setContentsMargins(0, 0, 0, 0)

        self.stats_label = QLabel()
        self.stats_label.setProperty("role", "muted")
        self.stats_label.setProperty("size", "small")
        stats_layout.addWidget(self.stats_label)
        stats_layout.addStretch()

        self._edit_hint = QLabel(tr("edit_hint"))
        self._edit_hint.setProperty("role", "warning-text")
        self._edit_hint.setProperty("size", "small")
        self._edit_hint.setVisible(False)
        stats_layout.addWidget(self._edit_hint)

        layout.addWidget(self.stats_bar)

        # Keyboard shortcuts
        find_sc = QShortcut(QKeySequence("Ctrl+F"), self)
        find_sc.activated.connect(self._open_find)

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
        self._update_display()
        self._set_buttons_enabled(True)

        word_count = len(result.full_text.split())
        self.stats_label.setText(tr(
            "transcript_stats",
            segments=len(result.segments),
            words=word_count,
            minutes=f"{result.duration / 60:.1f}",
        ))
        self.stats_bar.setVisible(True)

    def clear(self):
        self._result = None
        self._speaker_names = {}
        self.text_edit.clear()
        self._set_buttons_enabled(False)
        self.stats_bar.setVisible(False)
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

    # Below this width the header row (title + up to 5 buttons) no longer
    # fits and gets clipped by the splitter edge in RecordView — same
    # threshold class as FileSelector.set_compact()'s height check.
    _HEADER_COMPACT_WIDTH = 650

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_header_compact()

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

    def _set_buttons_enabled(self, enabled: bool):
        self.copy_btn.setEnabled(enabled)
        self.export_btn.setEnabled(enabled)
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

        if has_speakers and self._show_speakers:
            self._render_with_speakers()
        else:
            self._render_plain()

    def _render_plain(self):
        lines = []
        if self._show_timestamps:
            for seg in self._result.segments:
                ts = format_timestamp_vtt(seg.start)
                lines.append(f"[{ts}]  {seg.text.strip()}")
        else:
            for seg in self._result.segments:
                lines.append(seg.text.strip())
        self.text_edit.setPlainText('\n'.join(lines))
        self._highlighted_block = -1
        self._build_segment_map()

    def _render_with_speakers(self):
        theme = get_theme()
        html_lines = []
        for seg in self._result.segments:
            ts = format_timestamp_vtt(seg.start)
            name = html.escape(self._get_display_name(seg.speaker)) if seg.speaker else ""
            color = self._get_speaker_color(seg.speaker) if seg.speaker else theme.text_secondary
            text = html.escape(seg.text.strip())

            header = ""
            if self._show_timestamps or seg.speaker:
                name_span = f'<span style="color:{color};font-weight:500;font-size:{theme.font_sm};">{name}</span>' if name else ""
                ts_span = f'<span style="color:{theme.text_muted};font-size:{theme.font_xs};">[{ts}]</span>' if self._show_timestamps else ""
                space = "&nbsp;&nbsp;" if name and self._show_timestamps else ""
                header = f'{name_span}{space}{ts_span}<br>'

            # QTextEdit has limited CSS support, so we use a table for the bubble background
            bubble = f"""
            <table width="100%" cellpadding="10" cellspacing="0" style="margin-bottom: 8px;">
            <tr><td style="background-color: {theme.bg_base}; border: 1px solid {theme.border_input};">
                {header}
                <span style="color:{theme.text_primary}; font-size: {theme.font_md};">{text}</span>
            </td></tr>
            </table>
            """
            html_lines.append(bubble)

        self.text_edit.setHtml('<br>'.join(html_lines))
        self._highlighted_block = -1
        self._build_segment_map()

    # ------------------------------------------------------------------ edit mode

    def _toggle_edit(self, checked: bool):
        if checked:
            self._enter_edit_mode()
        else:
            self._exit_edit_mode(save=True)

    def _enter_edit_mode(self):
        self._edit_mode = True
        self.edit_btn.setChecked(True)
        self._edit_hint.setVisible(True)

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

        self.text_edit.setReadOnly(False)
        self.text_edit.setPlainText('\n'.join(lines))

    def _exit_edit_mode(self, save: bool = True):
        self._edit_mode = False
        self.edit_btn.setChecked(False)
        self._edit_hint.setVisible(False)
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
        dlg = _SpeakerRenameDialog(self._speaker_names, self)
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

    def _find_next(self):
        query = self._find_edit.text()
        if not query:
            return
        found = self.text_edit.find(query)
        if not found:
            # Wrap around
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.text_edit.setTextCursor(cursor)
            found = self.text_edit.find(query)
        self._update_find_count(query)

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
            self._find_next()
            return
        if not self._result:
            return
        for segment in self._result.segments:
            if query in segment.text:
                segment.text = segment.text.replace(query, replacement, 1)
                self._update_display()
                self.result_changed.emit("text")
                break
        self._find_next()

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
            self._find_count.setText(tr("find_count_plural", count=0))
            return
        for segment in self._result.segments:
            segment.text = segment.text.replace(query, replacement)
        self._update_display()
        self.result_changed.emit("text")
        self._find_count.setText(tr("find_replaced", count=count))

    def _update_find_count(self, query: str):
        text = self.text_edit.document().toPlainText()
        count = text.count(query)
        key = "find_count" if count == 1 else "find_count_plural"
        self._find_count.setText(tr(key, count=count))

    # ------------------------------------------------------------------ player sync

    def highlight_at(self, seconds: float):
        """Highlight the segment covering *seconds* (called by player ticker)."""
        if self._edit_mode or not self._segment_positions or not self._result:
            return

        target_block = -1
        for start, end, block_pos in self._segment_positions:
            if start <= seconds < end:
                target_block = block_pos
                break

        if target_block == self._highlighted_block:
            return

        doc = self.text_edit.document()

        if self._highlighted_block >= 0:
            prev = doc.findBlock(self._highlighted_block)
            if prev.isValid():
                fmt = QTextCharFormat()
                fmt.setBackground(QColor("transparent"))
                c = QTextCursor(prev)
                c.select(QTextCursor.SelectionType.BlockUnderCursor)
                c.mergeCharFormat(fmt)

        self._highlighted_block = target_block
        if target_block >= 0:
            block = doc.findBlock(target_block)
            if block.isValid():
                fmt = QTextCharFormat()
                fmt.setBackground(QColor(99, 102, 241, 40))
                c = QTextCursor(block)
                c.select(QTextCursor.SelectionType.BlockUnderCursor)
                c.mergeCharFormat(fmt)
                self.text_edit.setTextCursor(QTextCursor(block))
                self.text_edit.ensureCursorVisible()

    def _on_click(self, event):
        QTextEdit.mousePressEvent(self.text_edit, event)
        if self._edit_mode:
            return
        cursor = self.text_edit.cursorForPosition(event.pos())
        block_pos = cursor.block().position()
        for start, _end, seg_block_pos in self._segment_positions:
            if seg_block_pos == block_pos:
                self.seek_requested.emit(start)
                break

    def _build_segment_map(self):
        if not self._result:
            self._segment_positions = []
            return
        self._segment_positions = []
        doc = self.text_edit.document()
        block = doc.begin()
        seg_idx = 0
        segments = self._result.segments
        while block.isValid() and seg_idx < len(segments):
            text = block.text()
            seg = segments[seg_idx]
            fragment = seg.text.strip()[:20]
            if not fragment:
                # Segment with empty text: map to current block and move on.
                self._segment_positions.append((seg.start, seg.end, block.position()))
                seg_idx += 1
            elif fragment in text:
                self._segment_positions.append((seg.start, seg.end, block.position()))
                seg_idx += 1
            elif not text.strip():
                # Skip blank document blocks (e.g. HTML paragraph separators).
                block = block.next()
                continue
            block = block.next()
