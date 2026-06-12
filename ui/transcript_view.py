"""
Whisper Fedora UI - Transcript View Widget
Display, edit and search transcription results with timestamp and speaker support.
"""

import re
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QTextEdit, QLabel, QHBoxLayout,
    QPushButton, QFrame, QLineEdit, QDialog, QFormLayout,
    QDialogButtonBox, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal, QPoint
from PyQt6.QtGui import (
    QFont, QTextCharFormat, QColor, QTextCursor, QKeySequence, QShortcut
)

from transcriber import TranscriptionResult
from utils import format_timestamp_vtt
from ui.icons import get_icon, IconColors


# Speaker color palette (keyed by original speaker id)
SPEAKER_COLORS = {
    "Speaker 1": "#6366f1",
    "Speaker 2": "#22c55e",
    "Speaker 3": "#f59e0b",
    "Speaker 4": "#ef4444",
    "Speaker 5": "#06b6d4",
    "Speaker 6": "#ec4899",
    "Speaker 7": "#84cc16",
    "Speaker 8": "#8b5cf6",
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
        self.setWindowTitle("Rename Speakers")
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

        title = QLabel("Transcription")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Timestamps toggle
        self.timestamps_btn = QPushButton("Timestamps")
        self.timestamps_btn.setIcon(get_icon('clock', IconColors.DEFAULT, 14))
        self.timestamps_btn.setCheckable(True)
        self.timestamps_btn.setChecked(self._show_timestamps)
        self.timestamps_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timestamps_btn.clicked.connect(self._toggle_timestamps)
        header_layout.addWidget(self.timestamps_btn)

        # Speakers toggle (green accent when checked)
        self.speakers_btn = QPushButton("Speakers")
        self.speakers_btn.setIcon(get_icon('user', IconColors.DEFAULT, 14))
        self.speakers_btn.setCheckable(True)
        self.speakers_btn.setChecked(self._show_speakers)
        self.speakers_btn.setStyleSheet(
            "QPushButton:checkable:checked { border-color: #22c55e; color: #22c55e; }"
            "QPushButton:checkable:hover { background-color: rgba(34, 197, 94, 0.1); }"
        )
        self.speakers_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.speakers_btn.clicked.connect(self._toggle_speakers)
        self.speakers_btn.setVisible(False)
        header_layout.addWidget(self.speakers_btn)

        # Rename speakers button
        self.rename_btn = QPushButton("Rename…")
        self.rename_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.rename_btn.clicked.connect(self._rename_speakers)
        self.rename_btn.setVisible(False)
        header_layout.addWidget(self.rename_btn)

        # Edit toggle
        self.edit_btn = QPushButton("Edit")
        self.edit_btn.setCheckable(True)
        self.edit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.edit_btn.clicked.connect(self._toggle_edit)
        header_layout.addWidget(self.edit_btn)

        # Copy
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.setIcon(get_icon('clipboard', IconColors.DEFAULT, 14))
        self.copy_btn.setToolTip("Copy transcript  (Ctrl+Shift+C)")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.clicked.connect(self.copy_requested.emit)
        header_layout.addWidget(self.copy_btn)

        # Export
        self.export_btn = QPushButton("Export")
        self.export_btn.setIcon(get_icon('save', IconColors.WHITE, 14))
        self.export_btn.setProperty("variant", "primary")
        self.export_btn.setToolTip("Export transcript  (Ctrl+E)")
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_btn.clicked.connect(self.export_requested.emit)
        header_layout.addWidget(self.export_btn)

        layout.addWidget(header)

        # ── Text area ────────────────────────────────────────────
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Monospace", 11))
        self.text_edit.setPlaceholderText("Transcription will appear here…")
        self.text_edit.mousePressEvent = self._on_click
        layout.addWidget(self.text_edit, stretch=1)

        # ── Find/Replace bar (hidden by default) ─────────────────
        self._find_bar = QWidget()
        find_layout = QHBoxLayout(self._find_bar)
        find_layout.setContentsMargins(0, 0, 0, 0)
        find_layout.setSpacing(6)

        self._find_edit = QLineEdit()
        self._find_edit.setPlaceholderText("Find…")
        self._find_edit.returnPressed.connect(self._find_next)
        find_layout.addWidget(self._find_edit, stretch=1)

        self._replace_edit = QLineEdit()
        self._replace_edit.setPlaceholderText("Replace with…")
        find_layout.addWidget(self._replace_edit, stretch=1)

        find_next_btn = QPushButton("Next")
        find_next_btn.clicked.connect(self._find_next)
        find_layout.addWidget(find_next_btn)

        replace_btn = QPushButton("Replace")
        replace_btn.clicked.connect(self._replace_one)
        find_layout.addWidget(replace_btn)

        replace_all_btn = QPushButton("All")
        replace_all_btn.clicked.connect(self._replace_all)
        find_layout.addWidget(replace_all_btn)

        self._find_count = QLabel("")
        self._find_count.setStyleSheet("color: #888888; font-size: 11px;")
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
        self.stats_label.setStyleSheet("color: #888888; font-size: 11px;")
        stats_layout.addWidget(self.stats_label)
        stats_layout.addStretch()

        self._edit_hint = QLabel("Edit mode: one line per segment • click Save to apply")
        self._edit_hint.setStyleSheet("color: #f59e0b; font-size: 11px;")
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
        self.stats_label.setText(
            f"{len(result.segments)} segments  •  {word_count} words  •  {result.duration / 60:.1f} min"
        )
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

    def get_speaker_names(self) -> dict:
        return dict(self._speaker_names)

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
        self._update_display()

    def _toggle_speakers(self):
        self._show_speakers = self.speakers_btn.isChecked()
        self._update_display()

    def _get_display_name(self, speaker_id: str) -> str:
        return self._speaker_names.get(speaker_id, speaker_id)

    def _get_speaker_color(self, speaker_id: str) -> str:
        return SPEAKER_COLORS.get(speaker_id, "#888888")

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
        self.text_edit.setText('\n'.join(lines))
        self._highlighted_block = -1
        self._build_segment_map()

    def _render_with_speakers(self):
        html_lines = []
        for seg in self._result.segments:
            parts = []
            if self._show_timestamps:
                ts = format_timestamp_vtt(seg.start)
                parts.append(f'<span style="color:#666;">[{ts}]</span>')
            if seg.speaker:
                name = self._get_display_name(seg.speaker)
                color = self._get_speaker_color(seg.speaker)
                parts.append(f'<span style="color:{color};font-weight:bold;">[{name}]</span>')
            parts.append(f'<span style="color:#e0e0e0;">{seg.text.strip()}</span>')
            html_lines.append(' '.join(parts))
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
            self._parse_edited_text()

        self._update_display()

    def _parse_edited_text(self):
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
            return

        orig = self._result.segments
        new_segments: list = []
        for i, (start, speaker, text_parts) in enumerate(parsed):
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
                text=' '.join(text_parts).strip(),
                speaker=speaker,
            ))

        # full_text is a property over segments, so export/AI/copy auto-update.
        self._result.segments = new_segments

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
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == query:
            cursor.insertText(replacement)
        self._find_next()

    def _replace_all(self):
        query = self._find_edit.text()
        replacement = self._replace_edit.text()
        if not query:
            return
        doc = self.text_edit.document()
        text = doc.toPlainText()
        count = text.count(query)
        if count == 0:
            self._find_count.setText("0 matches")
            return
        new_text = text.replace(query, replacement)
        self.text_edit.setPlainText(new_text)
        self._find_count.setText(f"Replaced {count}")

    def _update_find_count(self, query: str):
        text = self.text_edit.document().toPlainText()
        count = text.count(query)
        self._find_count.setText(f"{count} match{'es' if count != 1 else ''}")

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
            if fragment and fragment in text:
                self._segment_positions.append((seg.start, seg.end, block.position()))
                seg_idx += 1
            elif not text.strip():
                block = block.next()
                continue
            block = block.next()
