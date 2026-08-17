"""Real-Qt test support.

This suite deliberately lives outside ``tests/`` so it never imports the
PyQt stand-ins installed by ``tests/conftest.py``.  Invoke it with the
project virtualenv and ``QT_QPA_PLATFORM=offscreen``.
"""

from __future__ import annotations

import os
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


@pytest.fixture
def process_events(qt_application):
    """Flush pending queued signals without sleeping in test code."""
    def _process() -> None:
        qt_application.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
    return _process
