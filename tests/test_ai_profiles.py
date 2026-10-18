"""Local AI profiles and prompt templates (N05): schema, application of a
profile to step params and cache keys, template storage and versions, and
the queue's frozen profile. A fake transport stands in for LM Studio — no
server is contacted."""

from __future__ import annotations

import pytest

import config
from application import ai_profiles, queue_store
from application.steps import llm_params
from core import prompt_templates
from core.prompts import load_prompt, prompt_version
from domain import ai_profile as m
from domain.recipe import Recipe


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    cfg = config.Config()
    monkeypatch.setattr(config, "_config", cfg)
    monkeypatch.setattr("core.paths.data_dir", lambda: tmp_path)
    return cfg


def _profile(**over):
    data = {"name": "Big", "endpoint": "http://localhost:5678/v1/", "model": "gemma-big",
            "context_chars": 80_000, "temperature": 0.1, "steps": ["article", "bogus"]}
    data.update(over)
    return m.profile_from_dict(data)


def test_profile_validation():
    p = _profile()
    assert p.endpoint == "http://localhost:5678/v1" and p.steps == ("article",) and p.is_local
    assert not _profile(endpoint="https://example.com/v1").is_local
    for bad in ({"name": ""}, {"endpoint": "localhost:1234"}, {"endpoint": "ftp://x"},
                {"context_chars": 10}, {"temperature": 3}, {"api_key": "sk-123"},
                {"schema_version": 99}, {"context_chars": "many"}):
        with pytest.raises(m.ProfileError):
            _profile(**bad)
    with pytest.raises(m.ProfileError):
        m.profile_from_dict("not an object")


def test_default_profile_is_the_plain_settings_and_changes_nothing(cfg):
    assert ai_profiles.active_profile(cfg).name == m.DEFAULT_PROFILE
    base = llm_params(cfg)
    assert base["lm_url"] == cfg.lm_studio_url and "model" not in base


def test_active_profile_overrides_endpoint_model_context_and_temperature(cfg):
    ai_profiles.save_profile(cfg, _profile())
    cfg.lm_profile_active = "Big"
    params = llm_params(cfg)
    assert params["lm_url"] == "http://localhost:5678/v1" == params["book_lm_url"]
    assert params["model"] == "gemma-big" == params["book_model"]
    assert params["insights_context_chars"] == 80_000 and params["book_temperature"] == 0.1
    # An explicit profile (a queued file's) beats whatever is active now.
    assert llm_params(cfg, profile=None)["lm_url"] == cfg.lm_studio_url
    cfg.lm_profile_active = "gone"
    assert ai_profiles.active_profile(cfg).name == m.DEFAULT_PROFILE   # removed → back to default


def test_changing_the_profile_changes_the_generation_cache_key(cfg):
    from application.steps import _generation_key, build_step_context
    from domain.transcription import Segment, TranscriptionResult

    result = TranscriptionResult(segments=[Segment(0.0, 1.0, "x")], language="en", duration=1.0)

    def key():
        context = build_step_context("/m/a.mp3", result, 1, params=llm_params(cfg))
        return _generation_key(context, "clean", "v1")

    before = key()
    ai_profiles.save_profile(cfg, _profile())
    cfg.lm_profile_active = "Big"
    assert key() != before


def test_save_rename_delete_and_active_follow(cfg):
    ai_profiles.save_profile(cfg, _profile())
    cfg.lm_profile_active = "Big"
    ai_profiles.save_profile(cfg, _profile(name="Bigger"), replace="Big")
    assert [p.name for p in ai_profiles.user_profiles(cfg)] == ["Bigger"]
    assert cfg.lm_profile_active == "Bigger"
    ai_profiles.delete_profile(cfg, "Bigger")
    assert cfg.lm_profile_active == "" and ai_profiles.user_profiles(cfg) == []
    cfg.lm_profiles = [{"name": "", "endpoint": "x"}, _profile().to_dict()]   # a corrupt entry is skipped
    assert [p.name for p in ai_profiles.user_profiles(cfg)] == ["Big"]


def test_queued_file_keeps_the_profile_it_was_queued_with(cfg):
    from application.queue_run import _queued_profile
    from domain.queue import QueueItem

    ai_profiles.save_profile(cfg, _profile())
    cfg.lm_profile_active = "Big"
    snapshot = queue_store.make_snapshot(Recipe(name="r", steps=("transcribe", "clean")), {})
    assert snapshot.llm["name"] == "Big"
    cfg.lm_profile_active = ""                       # the user switches back afterwards
    item = QueueItem(1, "b", 1, "/a.mp3", type(snapshot).from_json(snapshot.to_json()))
    assert _queued_profile(item).name == "Big"
    assert llm_params(cfg, profile=_queued_profile(item))["lm_url"] == "http://localhost:5678/v1"
    plain = queue_store.make_snapshot(Recipe(name="r", steps=("transcribe",)), {})
    assert plain.llm == {} and _queued_profile(QueueItem(2, "b", 2, "/b.mp3", plain)) is None


# ----------------------------------------------------------------- templates

def test_template_copy_edit_activate_and_versions(cfg):
    from core.prompts import PROMPTS_DIR

    file_before = (PROMPTS_DIR / "cleaning.md").read_bytes()
    builtin = load_prompt("cleaning")
    builtin_version = prompt_version("cleaning")
    template = prompt_templates.make_copy("cleaning", "My cleaning")
    assert template.text == builtin and template.version == 1
    prompt_templates.save(template)
    assert load_prompt("cleaning") == builtin          # saved, not active yet
    cfg.prompt_template_active = {"cleaning": "My cleaning"}
    assert load_prompt("cleaning") == builtin and prompt_version("cleaning") != builtin_version

    edited = m.template_from_dict({**template.to_dict(), "text": "Clean it my way.",
                                   "examples": [{"input": "uh hi", "output": "Hi."}]})
    saved = prompt_templates.save(edited)
    assert saved.version == 2
    assert load_prompt("cleaning").startswith("Clean it my way.") and "uh hi" in load_prompt("cleaning")
    v2 = prompt_version("cleaning")
    prompt_templates.save(m.template_from_dict(saved.to_dict()))   # no change → no new version
    assert prompt_templates.load("My cleaning").version == 2 and prompt_version("cleaning") == v2

    cfg.prompt_template_active = {}
    assert load_prompt("cleaning") == builtin and prompt_version("cleaning") == builtin_version
    assert (PROMPTS_DIR / "cleaning.md").read_bytes() == file_before   # the built-in is untouched


def test_template_validation_and_import_never_activates(cfg):
    names = set(prompt_templates.builtin_names())
    good = {"name": "T", "base": "cleaning", "text": "do it", "version": 3}
    assert m.template_from_dict(good, names).version == 3
    for bad in ({"base": "no-such-prompt"}, {"text": "  "}, {"name": ""}, {"version": "x"},
                {"schema_version": 99}):
        with pytest.raises(m.ProfileError):
            m.template_from_dict({**good, **bad}, names)
    prompt_templates.save(m.template_from_dict(good, names))
    assert cfg.prompt_template_active == {}
    assert load_prompt("cleaning") != "do it"


def test_a_broken_template_file_falls_back_to_the_builtin(cfg):
    builtin = load_prompt("cleaning")
    (prompt_templates.templates_dir() / "broken.json").write_text("{not json")
    cfg.prompt_template_active = {"cleaning": "broken"}
    assert load_prompt("cleaning") == builtin
