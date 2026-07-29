"""Unit tests for core/lm_status_worker.py — Qt stand-ins from conftest."""

import sys
from unittest.mock import MagicMock, patch

from core.lm_status_worker import LMStatusWorker


def _collect(worker) -> list[tuple[bool, str]]:
    """Record everything the worker emits on status_ready."""
    emitted: list[tuple[bool, str]] = []
    worker.status_ready.connect(lambda *args: emitted.append(args))
    return emitted


def _patched_client(probe_result=None, side_effect=None):
    """Patch LMStudioClient on whichever core.lm_client is loaded.

    conftest may have installed a stand-in module; the worker imports the
    symbol lazily inside _execute, so patching the attribute works either way.
    """
    client = MagicMock()
    if side_effect is not None:
        client.probe.side_effect = side_effect
    else:
        client.probe.return_value = probe_result
    factory = MagicMock(return_value=client)
    return patch.object(sys.modules["core.lm_client"], "LMStudioClient", factory), factory


class TestLMStatusWorker:
    def test_emits_the_probe_result(self):
        worker = LMStatusWorker("http://localhost:1234/v1")
        emitted = _collect(worker)
        ctx, factory = _patched_client((True, "gemma"))
        with ctx:
            worker._execute()

        assert emitted == [(True, "gemma")]
        factory.assert_called_once_with("http://localhost:1234/v1")

    def test_forwards_the_timeout(self):
        worker = LMStatusWorker("http://host/v1", timeout=1.5)
        _collect(worker)
        ctx, factory = _patched_client((True, ""))
        with ctx:
            worker._execute()

        factory.return_value.probe.assert_called_once_with(timeout=1.5)

    def test_reports_unreachable_without_raising(self):
        worker = LMStatusWorker("http://localhost:1234/v1")
        emitted = _collect(worker)
        ctx, _ = _patched_client((False, "connection refused"))
        with ctx:
            worker._execute()

        assert emitted == [(False, "connection refused")]

    def test_cancelled_worker_stays_silent(self):
        """A dialog closed mid-check must not get a late status update."""
        worker = LMStatusWorker("http://localhost:1234/v1")
        emitted = _collect(worker)
        worker.cancel()
        ctx, _ = _patched_client((True, "gemma"))
        with ctx:
            worker._execute()

        assert emitted == []

    def test_unexpected_exception_becomes_a_failed_status(self):
        """BaseWorker.run() routes exceptions to _on_error, which must not
        leave the caller's spinner running forever."""
        worker = LMStatusWorker("http://localhost:1234/v1")
        emitted = _collect(worker)
        ctx, _ = _patched_client(side_effect=RuntimeError("boom"))
        with ctx:
            worker.run()

        assert len(emitted) == 1
        connected, detail = emitted[0]
        assert connected is False
        assert "boom" in detail
