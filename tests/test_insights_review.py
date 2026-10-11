"""core.insights' second pass: titles picked from candidates, the
description fact-checked, and titles quoting numbers nobody said dropped."""

import json
import sys
import types

import pytest

from core.insights import _drop_unsaid_numbers, generate_insight
from domain.transcription import Segment

SEGMENTS = [Segment(0.0, 5.0, "We cut a month of work to one hour; 70 percent speed")]


class _ScriptedClient:
    """Answers chat_completion_stream calls from a script, recording prompts."""

    answers: list = []
    prompts: list = []

    def __init__(self, *_args, **_kwargs):
        pass

    def chat_completion_stream(self, **kwargs):
        type(self).prompts.append(kwargs["messages"][-1]["content"])
        return type(self).answers.pop(0)


@pytest.fixture()
def client(monkeypatch):
    stub = types.ModuleType("core.lm_client")
    stub.LMStudioClient = _ScriptedClient
    monkeypatch.setitem(sys.modules, "core.lm_client", stub)
    _ScriptedClient.answers = []
    _ScriptedClient.prompts = []
    return _ScriptedClient


def _run(insight_type):
    return generate_insight(insight_type, SEGMENTS, lm_url="x", max_transcript_chars=10_000)


def test_titles_are_picked_from_candidates(client):
    candidates = [f"Title {letter}" for letter in "ABCDEFGHIJKL"]
    client.answers = [json.dumps(candidates), json.dumps(["Title C", "Title A"])]
    assert _run("yt_titles") == ["Title C", "Title A"]
    review_prompt = client.prompts[1]
    assert "- Title A\n- Title B" in review_prompt
    assert "{draft}" not in review_prompt
    # The review keeps the shared transcript prefix.
    assert review_prompt.startswith(client.prompts[0].split("\n\n")[0])


def test_description_review_sees_the_draft_as_text(client):
    client.answers = ['["Draft hook.\\n\\nWe talked."]', '["Fixed hook.\\n\\nWe talked."]']
    assert _run("yt_description") == ["Fixed hook.\n\nWe talked."]
    assert "Draft hook.\n\nWe talked." in client.prompts[1]


@pytest.mark.parametrize("bad_review", ["not json", "[]", '[""]', None])
def test_unusable_review_keeps_the_draft(client, bad_review):
    client.answers = ['["Hook.\\n\\nWe talked."]', bad_review]
    assert _run("yt_description") == ["Hook.\n\nWe talked."]


def test_unreviewed_types_make_one_request(client):
    client.answers = ['["tag one", "tag two"]']
    assert _run("yt_tags") == ["tag one", "tag two"]
    assert len(client.prompts) == 1


def test_titles_with_unsaid_numbers_are_dropped():
    titles = ["3 Ways to Slow Down", "From a Month to One Hour", "The 70/30 Rule"]
    assert _drop_unsaid_numbers(titles, SEGMENTS) == ["From a Month to One Hour"]
    segments = SEGMENTS + [Segment(5.0, 9.0, "say 30 and 3")]
    assert _drop_unsaid_numbers(titles, segments) == titles
    # Never empties the list.
    assert _drop_unsaid_numbers(["5 Mistakes"], SEGMENTS) == ["5 Mistakes"]
    assert _drop_unsaid_numbers("raw text", SEGMENTS) == "raw text"


def test_one_string_answer_split_by_the_review_is_joined_back(client):
    client.answers = ['["Hook.\\n\\nWe talked."]', '["Better hook.", "We talked more."]']
    assert _run("yt_description") == ["Better hook.\n\nWe talked more."]
