"""The one-row player: speed menu and steps, click-to-seek on the slider,
and single-key playback shortcuts that work in the read-only transcript
but not while typing."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest


def test_speed_steps_stay_within_the_menu(process_events):
    from ui.player_widget import SPEEDS, PlayerWidget

    player = PlayerWidget()
    assert player.speed() == 1.0
    player.speed_step(1)
    assert player.speed() == 1.25
    assert player._speed_btn.text() == "1.25×"
    assert player._speed_actions[1.25].isChecked()
    for _ in range(20):
        player.speed_step(1)
    assert player.speed() == SPEEDS[-1]
    for _ in range(20):
        player.speed_step(-1)
    assert player.speed() == SPEEDS[0]


def test_a_click_on_the_groove_jumps_there(process_events):
    from ui.player_widget import PlayerWidget

    player = PlayerWidget()
    player.resize(800, 60)
    player.show()
    process_events()
    slider = player._slider
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(int(slider.width() * 0.75), slider.height() // 2))
    assert slider.value() > 600
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(int(slider.width() * 0.75), slider.height() // 2))


def test_playback_keys_work_in_the_reading_transcript_not_while_typing(monkeypatch, process_events):
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    process_events()
    calls = []
    monkeypatch.setattr(window.player, "toggle_play", lambda: calls.append("play"))

    window.transcript_view.text_edit.setFocus()
    process_events()
    assert window.transcript_view.text_edit.isReadOnly()
    window._space_play_pause()
    assert calls == ["play"]

    window.library_view._search_edit.setFocus()
    process_events()
    window._space_play_pause()
    assert calls == ["play"]

    window.close()
    process_events()
