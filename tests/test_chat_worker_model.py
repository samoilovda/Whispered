"""core/chat_worker.py keeps which model answered (audit 2026-11-04 AR4:
the panel read a Config field that never existed, so every chat message was
stored without its model)."""

from __future__ import annotations


def test_the_worker_records_the_loaded_model(monkeypatch):
    import sys
    import types

    from core.chat_worker import ChatWorker

    class Client:
        def __init__(self, url):
            pass

        def get_loaded_model(self, timeout=5):
            return "google/gemma-4-12b"

        def chat_completion_stream(self, **_kw):
            return "ответ"

    # Other suites swap sys.modules["core.lm_client"]; use a module of our own.
    monkeypatch.setitem(sys.modules, "core.lm_client", types.SimpleNamespace(LMStudioClient=Client))
    worker = ChatWorker(messages=[{"role": "user", "content": "?"}], lm_url="http://x/v1")
    finished = []
    worker.turn_finished.connect(finished.append)
    worker._execute()
    assert worker.model_name == "google/gemma-4-12b"


def test_no_config_field_is_read_by_a_name_it_does_not_have():
    """getattr(cfg, "<name>", default) hides a typo from mypy; the code reads
    Config fields as attributes now."""
    import dataclasses
    import re
    from pathlib import Path

    import config

    fields = {f.name for f in dataclasses.fields(config.Config)}
    root = Path(__file__).resolve().parent.parent
    pattern = re.compile(r'getattr\(\s*(?:cfg|get_config\(\)|self\._cfg)\s*,\s*"([a-z_0-9]+)"')
    unknown = []
    for path in root.glob("**/*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith((".venv/", "tests", "docs/")):
            continue
        for name in pattern.findall(path.read_text(encoding="utf-8")):
            if name not in fields:
                unknown.append(f"{rel}: {name}")
    assert unknown == []
