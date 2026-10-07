"""Y3 (docs/UI_CONCEPT_IMPLEMENTATION_PLAN_2026-10.ru.md): the user edits
YouTube chapters; the edits live in youtube_package.user.json beside the
step's result, never in it, and win over it until the user says otherwise.
"""

from __future__ import annotations

import json

from transcriber import Segment

_MODEL = [
    {"start": 0, "title": "Intro"},
    {"start": 60, "title": "Body"},
    {"start": 120, "title": "Outro"},
]


def _payload(chapters=None) -> dict:
    return {
        "chapters": list(_MODEL if chapters is None else chapters),
        "yt_titles": ["T"],
        "yt_description": ["Hook."],
        "yt_tags": ["a"],
        "yt_questions": [],
    }


def _panel(record_id=7, source="/media/talk.mp4"):
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    panel.set_provenance(record_id, source)
    panel.set_segments([Segment(0.0, 600.0, "hello")], transcript_language="en")
    panel.begin_generating()
    panel.set_result(_payload())
    return panel


def _edit(panel, text: str) -> None:
    panel._chapters_edit_btn.click()
    assert not panel._chapters_edit.isReadOnly()
    panel._chapters_edit.setPlainText(text)
    panel._chapters_done_btn.click()


def _overlay_file(panel):
    return panel._overlay_file()


def test_edit_mode_shows_raw_lines_and_hides_rows():
    panel = _panel()
    panel._chapters_edit_btn.click()
    assert panel._chapters_edit.toPlainText() == "0:00 Intro\n1:00 Body\n2:00 Outro"
    assert not panel._chapter_scroll.isVisibleTo(panel)
    assert panel._chapters_done_btn.isVisibleTo(panel)
    panel._chapters_cancel_btn.click()
    assert panel._chapters_edit.isReadOnly()
    assert panel._chapter_scroll.isVisibleTo(panel)
    panel.close()


def test_edit_is_saved_beside_the_result_and_used_everywhere():
    panel = _panel()
    _edit(panel, "0:00 Hello\n1:30 Middle\n2:00 Outro")

    path = _overlay_file(panel)
    assert path.name == "youtube_package.user.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["chapters"][1] == {"start": 90, "title": "Middle"}
    # The step's own result file is never written by the panel.
    assert not path.with_name("youtube_package.json").exists()

    assert panel._chapters_edit.toPlainText().splitlines()[1] == "1:30 Middle"
    assert panel.publish_texts()["description"].endswith("0:00 Hello\n1:30 Middle\n2:00 Outro")
    assert [c[1] for c in panel.publish_texts()["chapter_check"].chapters] == [
        "Hello", "Middle", "Outro",
    ]
    assert "✎" in panel._chapters_note.text()
    assert panel._chapters_reset_btn.isVisibleTo(panel)
    panel.close()


def test_edits_survive_a_rerun_with_the_same_model_result():
    panel = _panel()
    _edit(panel, "0:00 Hello\n1:00 Body\n2:00 Outro")
    panel.begin_generating()
    panel.set_result(_payload())
    assert panel._chapters_data[0]["title"] == "Hello"
    assert not panel._chapters_keep_btn.isVisibleTo(panel)   # not stale
    panel.close()


def test_new_model_chapters_are_flagged_and_the_user_chooses():
    panel = _panel()
    _edit(panel, "0:00 Hello\n1:00 Body\n2:00 Outro")
    newer = _MODEL + [{"start": 300, "title": "Bonus"}]

    panel.begin_generating()
    panel.set_result(_payload(newer))
    assert panel._chapters_data[0]["title"] == "Hello"         # still the user's
    assert panel._chapters_keep_btn.isVisibleTo(panel)
    assert panel._chapters_note.property("role") == "warning-text"

    panel._chapters_keep_btn.click()
    assert not panel._chapters_keep_btn.isVisibleTo(panel)
    assert panel._chapters_data[0]["title"] == "Hello"

    panel.begin_generating()
    panel.set_result(_payload(newer))
    assert not panel._chapters_keep_btn.isVisibleTo(panel)     # choice remembered

    panel._chapters_reset_btn.click()                          # back to the model
    assert [c["title"] for c in panel._chapters_data] == ["Intro", "Body", "Outro", "Bonus"]
    assert not _overlay_file(panel).exists()
    panel.close()


def test_unchanged_save_records_no_edit():
    panel = _panel()
    _edit(panel, "0:00 Intro\n1:00 Body\n2:00 Outro")
    assert not _overlay_file(panel).exists()
    assert not panel._chapters_reset_btn.isVisibleTo(panel)
    panel.close()


def test_bad_lines_keep_the_editor_open():
    panel = _panel()
    _edit(panel, "0:00 Intro\nnot a chapter\n2:00 Outro")
    assert not panel._chapters_edit.isReadOnly()
    assert "2" in panel._chapters_note.text()
    assert not _overlay_file(panel).exists()
    panel.close()


def test_chapters_youtube_would_drop_can_be_fixed_by_hand():
    panel = _panel()
    panel.begin_generating()
    panel.set_result(_payload([
        {"start": 0, "title": "Intro"},
        {"start": 4, "title": "Squeezed"},
        {"start": 60, "title": "Body"},
    ]))
    assert not panel._chapter_check.shows_on_youtube
    panel._chapters_edit_btn.click()
    assert "0:04 Squeezed" in panel._chapters_edit.toPlainText()   # visible to fix
    panel._chapters_edit.setPlainText("0:00 Intro\n0:30 Squeezed\n1:00 Body")
    panel._chapters_done_btn.click()
    assert panel._chapter_check.shows_on_youtube
    panel.close()


def test_insert_player_time_adds_a_line():
    panel = _panel()
    panel.set_position_provider(lambda: 95.4)
    panel._chapters_edit_btn.click()
    panel._chapters_insert_btn.click()
    assert panel._chapters_edit.toPlainText().endswith("2:00 Outro\n1:35 ")
    panel.close()


def test_insert_is_disabled_without_a_player():
    panel = _panel()
    panel._chapters_edit_btn.click()
    assert not panel._chapters_insert_btn.isEnabled()
    panel.close()


def test_edits_are_per_record():
    first = _panel(record_id=1, source="/media/a.mp4")
    _edit(first, "0:00 Hello\n1:00 Body\n2:00 Outro")
    second = _panel(record_id=2, source="/media/b.mp4")
    assert second._chapters_data[0]["title"] == "Intro"
    first.close()
    second.close()


def test_main_window_gives_the_panel_the_player_position():
    from ui.main_window import MainWindow

    window = MainWindow()
    assert window.youtube_panel._position_provider is not None
    window.close()


def test_edit_buttons_have_captions_from_the_start():
    # Retranslator.call() only re-runs on a language change, so captions
    # must be set when the buttons are built.
    from ui.youtube_panel import YouTubePanel

    panel = YouTubePanel()
    for button in (
        panel._chapters_edit_btn, panel._chapters_insert_btn, panel._chapters_cancel_btn,
        panel._chapters_done_btn, panel._chapters_reset_btn, panel._chapters_keep_btn,
    ):
        assert button.text()
    panel.close()
