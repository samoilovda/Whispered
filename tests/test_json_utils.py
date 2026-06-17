"""Tests for core.json_utils — defensive parsing of LLM output."""

from core.json_utils import extract_json, strip_fences


def test_extract_plain_object():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_fenced_json():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_fenced_untagged():
    assert extract_json('```\n{"a": 1}\n```') == {"a": 1}


def test_extract_object_with_surrounding_prose():
    text = 'Sure, here is the result:\n{"topics": ["x"]}\nHope it helps!'
    assert extract_json(text) == {"topics": ["x"]}


def test_extract_array():
    assert extract_json('[1, 2, 3]') == [1, 2, 3]


def test_malformed_returns_default():
    # The old code raised IndexError on a lone fence — must not anymore.
    assert extract_json('```') is None
    assert extract_json('not json at all') is None
    assert extract_json('', default={}) == {}
    assert extract_json(None) is None


def test_default_is_returned():
    assert extract_json('garbage', default={"fallback": True}) == {"fallback": True}


def test_strip_fences_without_fence():
    assert strip_fences('hello world') == 'hello world'


def test_strip_fences_with_fence():
    assert strip_fences('```python\ncode\n```') == 'code'
