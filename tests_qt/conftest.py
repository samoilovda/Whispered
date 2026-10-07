"""Real-Qt test support.

This suite deliberately lives outside ``tests/`` so it never imports the
PyQt stand-ins installed by ``tests/conftest.py``.  Invoke it with the
project virtualenv and ``QT_QPA_PLATFORM=offscreen``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication, QEventLoop
from PyQt6.QtWidgets import QApplication


# ``core.paths`` resolves the macOS data directory at import time.  Point it
# at a disposable location before any application module is imported so the
# smoke suite never reads or writes the developer's real Library database.
_TEST_HOME = Path(tempfile.mkdtemp(prefix="whispered-qt-"))
os.environ["HOME"] = str(_TEST_HOME)


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    """Provide one real QApplication for the complete regression suite."""
    os.environ.setdefault("WHISPERED_UI_GALLERY", "1")
    app = QApplication.instance() or QApplication([])
    yield app
    QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)


@pytest.fixture(autouse=True)
def no_real_lm_studio_probes(monkeypatch):
    """Smoke tests build real panels (Book, AI, Settings) that fire an async
    LM Studio reachability probe on a timer/click. Left unstubbed, these hit
    a real socket — slow, non-deterministic, and liable to still be in
    flight when the process exits, which aborts the interpreter with
    ``QThread: Destroyed while thread is still running`` (see
    core/lm_status_worker.py). Offline-first applies to tests too: nothing
    here should depend on a real LM Studio instance being reachable.
    """
    from core.lm_client import LMStudioClient

    monkeypatch.setattr(
        LMStudioClient, "probe", lambda self, timeout=5: (False, "stubbed-offline")
    )
    # Generation too: a recipe run in a test (clean, article, insights…)
    # otherwise reaches a real LM Studio when one happens to be running on
    # the developer's machine — the run then waits on a real model and the
    # test's bounded wait() times out (four test_recipe_retry tests failed
    # that way locally while passing in CI). Offline: "no response".
    monkeypatch.setattr(LMStudioClient, "check_connection", lambda self, timeout=5: False)
    monkeypatch.setattr(LMStudioClient, "get_loaded_model", lambda self, timeout=5: None)
    monkeypatch.setattr(LMStudioClient, "complete", lambda self, *a, **k: None)
    monkeypatch.setattr(LMStudioClient, "chat_completion", lambda self, *a, **k: None)
    monkeypatch.setattr(LMStudioClient, "chat_completion_stream", lambda self, *a, **k: None)


@pytest.fixture
def process_events(qt_application):
    """Flush pending queued signals without sleeping in test code."""
    def _process() -> None:
        qt_application.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
    return _process


@pytest.fixture(autouse=True)
def _isolated_artifact_output_dir():
    """Wipe ``core.paths.output_dir()`` before every test.

    ``_TEST_HOME`` above is set once per session, so every test in this
    suite shares one on-disk ``output/`` tree. Most tests use the same
    default ``record_id="unsaved"``/stem "recording" (or similar) inputs,
    and B1's cache-skip (``application/steps.py::build_cache_checks``)
    treats an unchanged transcript/params pair as a hit regardless of
    which test wrote it — without this, a later test's step gets silently
    SKIPPED and fed an earlier test's stale artifact instead of exercising
    the fake/failing generator it just installed.
    """
    from core.paths import output_dir

    path = output_dir()
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _youtube_publish_defaults():
    """The publish dialog opens by itself after a YouTube run when
    ``yt_publish_mode`` is on, and its ``exec()`` would block a test. Keep
    the mode (and the OAuth fields tests poke at) from leaking between tests
    through the process-wide Config."""
    from config import get_config

    cfg = get_config()
    saved = (cfg.yt_publish_mode, cfg.yt_oauth_client_id,
             cfg.yt_oauth_client_secret, cfg.yt_channel_title)
    cfg.yt_publish_mode = "off"
    yield
    (cfg.yt_publish_mode, cfg.yt_oauth_client_id,
     cfg.yt_oauth_client_secret, cfg.yt_channel_title) = saved
