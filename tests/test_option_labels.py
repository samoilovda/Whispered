"""Unit tests for ui/option_labels.py's model-download state (B10, see
docs/IMPROVEMENT_PLAN_2026-08.ru.md).
"""

from __future__ import annotations

from core.i18n import load_locale, tr
from ui.option_labels import (
    model_state_suffix,
    whisper_model_options_with_state,
    whisper_model_options,
)
from utils import WHISPER_MODELS


def test_no_downloaded_models_reports_every_key_as_not_downloaded(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.get_models_dir", lambda: str(tmp_path))

    result = whisper_model_options_with_state()

    assert [key for key, _label, _downloaded in result] == [k for k, _ in WHISPER_MODELS]
    assert all(downloaded is False for _key, _label, downloaded in result)


def test_a_present_file_of_the_expected_size_is_reported_downloaded(tmp_path, monkeypatch):
    from core.model_manifest import MANIFEST

    monkeypatch.setattr("utils.get_models_dir", lambda: str(tmp_path))
    entry = MANIFEST["whisper-tiny"]
    (tmp_path / "ggml-tiny.bin").write_bytes(b"\0" * entry.size_bytes)

    result = dict((key, downloaded) for key, _label, downloaded in whisper_model_options_with_state())

    assert result["tiny"] is True
    assert result["base"] is False


def test_a_present_file_of_the_wrong_size_is_not_reported_downloaded(tmp_path, monkeypatch):
    """A partial/corrupt download must not read as ready."""
    from core.model_manifest import MANIFEST

    monkeypatch.setattr("utils.get_models_dir", lambda: str(tmp_path))
    entry = MANIFEST["whisper-tiny"]
    (tmp_path / "ggml-tiny.bin").write_bytes(b"\0" * (entry.size_bytes - 1))

    result = dict((key, downloaded) for key, _label, downloaded in whisper_model_options_with_state())

    assert result["tiny"] is False


def test_a_model_with_no_manifest_entry_falls_back_to_existence_only(tmp_path, monkeypatch):
    """large-v3-turbo-q5_0/q8_0 have no core.model_manifest entry at
    all — any non-empty file at the expected path must count."""
    monkeypatch.setattr("utils.get_models_dir", lambda: str(tmp_path))
    (tmp_path / "ggml-large-v3-turbo-q5_0.bin").write_bytes(b"\0" * 123)

    result = dict((key, downloaded) for key, _label, downloaded in whisper_model_options_with_state())

    assert result["large-v3-turbo-q5_0"] is True


def test_labels_match_whisper_model_options(tmp_path, monkeypatch):
    """The state-aware variant must not diverge from the plain one's
    labels — only the extra downloaded flag is new."""
    monkeypatch.setattr("utils.get_models_dir", lambda: str(tmp_path))

    plain = whisper_model_options()
    with_state = whisper_model_options_with_state()

    assert [(k, label) for k, label in plain] == [(k, label) for k, label, _ in with_state]


def test_model_state_suffix_is_a_distinct_text_for_each_state():
    load_locale("en")
    downloaded = model_state_suffix(True)
    not_downloaded = model_state_suffix(False)
    assert downloaded != not_downloaded
    assert downloaded == tr("model_state_downloaded")
    assert not_downloaded == tr("model_state_not_downloaded")
