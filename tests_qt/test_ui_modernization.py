"""Tests for modernized UI components:
- WorkspaceShell 4-icon Mini-Rail (Library, New, Search ⌘K, Queue)
- StartView segmented source bar & visual step pipeline chips
- CommandPalette icons and role
- PlayerWidget _WaveformSlider (audio peaks, segment envelope, cut ranges)
- TranscriptView inline WYSIWYG segment editor
"""

from __future__ import annotations

import math
import struct
import wave

from PyQt6.QtWidgets import QLabel

from domain.transcription import Segment, TranscriptionResult
from ui.command_palette import CommandPalette
from ui.player_widget import PlayerWidget, _extract_wav_peaks
from ui.transcript_view import TranscriptView
from ui.workspace_shell import WorkspaceShell


def test_workspace_shell_compact_mini_rail_emits_palette_and_queue(monkeypatch, process_events):
    from PyQt6.QtWidgets import QWidget
    from config import get_config

    monkeypatch.setattr("ui.workspace_shell.save_config", lambda: True)
    monkeypatch.setattr(get_config(), "library_collapsed", False)

    shell = WorkspaceShell(QWidget(), QWidget())
    shell.resize(900, 600)
    shell.show()
    shell.set_library_collapsed(True)
    process_events()

    assert shell.library_button.isVisible()
    assert shell.search_button.isVisible()
    assert shell.queue_button.isVisible()

    palette_hits: list[bool] = []
    queue_hits: list[bool] = []
    shell.palette_requested.connect(lambda: palette_hits.append(True))
    shell.queue_requested.connect(lambda: queue_hits.append(True))

    shell.search_button.click()
    shell.queue_button.click()
    assert palette_hits == [True]
    assert queue_hits == [True]
    shell.close()


def test_start_view_builds_segmented_bar_and_step_pipeline_chips(process_events):
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()
    view = window.start_view
    assert view._source_bar.property("role") == "segmented-bar"
    view.set_recipe("youtube_video")
    process_events()
    chips = [
        lbl.text()
        for lbl in view._steps_pipeline_widget.findChildren(QLabel)
        if lbl.property("role") == "chip"
    ]
    assert len(chips) >= 2
    window.close()


def test_command_palette_assigns_icons_and_role(process_events):
    palette = CommandPalette()
    assert palette.property("role") == "command-palette"
    palette._refresh("")
    icons_found = 0
    for i in range(palette.results.count()):
        item = palette.results.item(i)
        if item.data(0x0100) is not None:  # Qt.ItemDataRole.UserRole
            assert not item.icon().isNull()
            icons_found += 1
    assert icons_found > 0
    palette.close()


def test_waveform_slider_extracts_wav_peaks_and_segment_envelope(tmp_path, process_events):
    wav_path = tmp_path / "tone.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        samples = [int(16000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(8000)]
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))

    peaks = _extract_wav_peaks(str(wav_path), bins=32)
    assert len(peaks) == 32
    assert max(peaks) >= 0.9

    player = PlayerWidget()
    player.resize(600, 48)
    player.show()
    segments = [
        Segment(0.0, 4.0, "First spoken sentence with steady cadence."),
        Segment(5.0, 10.0, "Second spoken sentence after a brief pause."),
    ]
    player.set_segments_envelope(segments, fallback_duration=10.0, bins=40)
    assert len(player._slider.peaks()) == 40
    player.set_cut_ranges([(5.0, 10.0)], fallback_duration=10.0)
    assert player._slider.cut_ranges() == [(0.5, 1.0)]
    player.repaint()
    process_events()
    player.close()


def test_transcript_wysiwyg_inline_segment_editor_updates_text_and_speaker(process_events):
    view = TranscriptView()
    view.resize(900, 600)
    view.show()
    segments = [
        Segment(0.0, 3.0, "Initial phrase.", speaker="Speaker 1"),
        Segment(3.0, 6.0, "Second phrase.", speaker="Speaker 2"),
    ]
    view.set_result(TranscriptionResult(segments=segments, language="en", duration=6.0))
    process_events()

    changes: list[str] = []
    view.result_changed.connect(changes.append)

    view.open_wysiwyg_segment(0)
    assert view._wysiwyg_bar.isVisible()
    assert view._wysiwyg_text_edit.text() == "Initial phrase."
    assert "00:00" in view._wysiwyg_time_label.text()

    view._wysiwyg_text_edit.setText("Updated phrase without touching timestamps.")
    view._wysiwyg_speaker_combo.setEditText("Alice")
    assert view.apply_wysiwyg_edit() is True

    res = view.get_result()
    assert res is not None
    assert res.segments[0].text == "Updated phrase without touching timestamps."
    assert res.speaker_names.get("Speaker 1") == "Alice"
    assert "text" in changes
    assert "speakers" in changes

    view.step_wysiwyg_segment(1)
    assert view._wysiwyg_index == 1
    assert view._wysiwyg_text_edit.text() == "Second phrase."

    view.close_wysiwyg_editor()
    assert not view._wysiwyg_bar.isVisible()
    view.close()
