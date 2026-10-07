"""Unit tests for application/user_edits.py — no Qt required."""

from pathlib import Path

from application.user_edits import (
    drop_edit,
    fingerprint,
    is_stale,
    load_overlay,
    overlay_path,
    rebase_edit,
    save_overlay,
    set_edit,
)

_MODEL = [{"start": 0, "title": "Intro"}, {"start": 60, "title": "Body"}]
_EDITED = [{"start": 0, "title": "Hello"}, {"start": 60, "title": "Body"}]


def test_overlay_path_sits_next_to_the_result():
    assert overlay_path(Path("/a/youtube_package.json")) == Path("/a/youtube_package.user.json")


def test_round_trip(tmp_path):
    path = tmp_path / "x.user.json"
    data = set_edit({}, "chapters", _EDITED, _MODEL)
    save_overlay(path, data)
    assert load_overlay(path) == data


def test_missing_or_broken_overlay_is_empty(tmp_path):
    assert load_overlay(tmp_path / "missing.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_overlay(broken) == {}
    listy = tmp_path / "list.json"
    listy.write_text("[1, 2]", encoding="utf-8")
    assert load_overlay(listy) == {}


def test_empty_overlay_removes_the_file(tmp_path):
    path = tmp_path / "x.user.json"
    save_overlay(path, {"chapters": _EDITED})
    save_overlay(path, {})
    assert not path.exists()
    save_overlay(path, {})          # removing twice is fine


def test_save_leaves_no_temp_files(tmp_path):
    save_overlay(tmp_path / "x.user.json", {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["x.user.json"]


def test_edit_equal_to_the_model_is_no_edit():
    data = set_edit({"other": 1}, "chapters", _MODEL, _MODEL)
    assert data == {"other": 1}


def test_staleness_follows_the_model():
    data = set_edit({}, "chapters", _EDITED, _MODEL)
    assert not is_stale(data, "chapters", _MODEL)
    newer = _MODEL + [{"start": 120, "title": "End"}]
    assert is_stale(data, "chapters", newer)
    assert not is_stale(rebase_edit(data, "chapters", newer), "chapters", newer)
    assert not is_stale({}, "chapters", newer)


def test_drop_edit():
    data = set_edit({}, "chapters", _EDITED, _MODEL)
    assert drop_edit(data, "chapters") == {}


def test_fingerprint_ignores_key_order():
    assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})
