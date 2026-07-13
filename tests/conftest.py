"""Shared Qt stubs for the whole test suite.

No test needs a real PyQt6 install. Every test module used to install its
own ad-hoc stand-ins for PyQt6 (QObject/QThread/pyqtSignal) directly into
``sys.modules`` via ``setdefault``/direct assignment. Because ``sys.modules``
is shared for the whole pytest session, whichever file's stub classes were
inserted first "won" for every other file collected afterwards — and some of
those per-file stubs were incompatible with what other files' code expected
(e.g. a ``QThread`` stub with no ``__init__(parent=...)``), which could hang
or crash the full-suite run depending on collection order.

This module installs one canonical, permissive set of stand-ins *before* any
test module is imported, so ``import core`` (and everything under it) always
succeeds the same way regardless of which test file triggers it first.

Test files that need a *specific* fake (e.g. a scripted ``LMStudioClient``
that returns canned responses) still install their own module in
``sys.modules['core.lm_client']`` via **direct assignment** — that always
wins over the generic default installed here, so those are unaffected.
"""

from __future__ import annotations

import sys
import types


class _BoundSignal:
    """Per-instance connect()/emit() stand-in for a bound pyqtSignal."""

    def __init__(self):
        self.calls: list = []

    def connect(self, slot):
        self.calls.append(slot)

    def disconnect(self, slot=None):
        if slot is None:
            self.calls.clear()
            return
        try:
            self.calls.remove(slot)
        except ValueError:
            raise TypeError("slot is not connected")

    def emit(self, *args):
        for slot in list(self.calls):
            slot(*args)


class _FakeSignal:
    """Class-level pyqtSignal descriptor stand-in.

    ``pyqtSignal(...)`` is called at class-body time (e.g.
    ``finished = pyqtSignal(str, object)``); the returned descriptor hands
    out one independent ``_BoundSignal`` per owning instance, matching real
    Qt signal semantics closely enough for unit tests.
    """

    def __init__(self, *args, **kwargs):
        self._attr = None

    def __set_name__(self, owner, name):
        self._attr = f"_bound_signal_{name}"

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        attr = self._attr or f"_bound_signal_{id(self)}"
        if not hasattr(obj, attr):
            setattr(obj, attr, _BoundSignal())
        return getattr(obj, attr)


class _FakeQObject:
    def __init__(self, *args, **kwargs):
        pass

    def deleteLater(self):
        pass


class _FakeQThread(_FakeQObject):
    """``start()`` is deliberately a no-op: real thread execution is out of
    scope for unit tests. Tests that want a worker's logic to actually run
    call its ``_execute()``/``run()`` method directly instead of ``start()``.
    """

    finished = _FakeSignal()

    def start(self):
        pass

    def isRunning(self):
        return False

    def wait(self, *args, **kwargs):
        return True

    def quit(self):
        pass

    def terminate(self):
        pass


def _install_pyqt6_stubs() -> None:
    for name in (
        "PyQt6", "PyQt6.QtCore", "PyQt6.QtWidgets", "PyQt6.QtGui", "PyQt6.QtMultimedia",
    ):
        sys.modules.setdefault(name, types.ModuleType(name))

    qtcore = sys.modules["PyQt6.QtCore"]
    qtcore.QObject = _FakeQObject
    qtcore.QThread = _FakeQThread
    qtcore.pyqtSignal = _FakeSignal


def _install_core_stubs() -> None:
    """Stub the Qt-facing core submodules that ``core/__init__.py`` imports
    at package-init time, so any ``from core.X import Y`` succeeds cleanly
    the first time it's triggered, no matter which test file does it first.
    """
    if "core.lm_client" not in sys.modules:
        lm_stub = types.ModuleType("core.lm_client")
        lm_stub.LMStudioClient = object
        lm_stub.DEFAULT_LM_STUDIO_URL = "http://localhost:1234/v1"
        sys.modules["core.lm_client"] = lm_stub

    if "core.ai_worker" not in sys.modules:
        ai_stub = types.ModuleType("core.ai_worker")
        ai_stub.AIProcessingWorker = object
        sys.modules["core.ai_worker"] = ai_stub


_install_pyqt6_stubs()
_install_core_stubs()
