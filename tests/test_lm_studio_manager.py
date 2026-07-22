"""LM Studio CLI control must stay cancellable and non-interactive."""

import subprocess
import threading

from lm_studio_manager import LMStudioManager


class _FakeProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return 0 if self.terminated or self.killed else None

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def communicate(self, timeout=None):
        if self.terminated or self.killed:
            return "", ""
        raise subprocess.TimeoutExpired("lms", timeout)


def test_cancelled_cli_process_is_terminated(monkeypatch):
    manager = LMStudioManager()
    manager._cli_path = "/fake/lms"
    process = _FakeProcess()
    monkeypatch.setattr("lm_studio_manager.subprocess.Popen", lambda *_a, **_kw: process)
    cancelled = threading.Event()
    cancelled.set()

    success, output = manager._run_cli(
        ["ls", "--json"], timeout=30, cancel_event=cancelled
    )

    assert not success
    assert output == "Cancelled"
    assert process.terminated


def test_model_load_is_always_non_interactive(monkeypatch):
    manager = LMStudioManager()
    captured = {}

    def fake_run(args, timeout=30, cancel_event=None):
        captured.update(args=args, timeout=timeout, cancel_event=cancel_event)
        return True, ""

    monkeypatch.setattr(manager, "_run_cli", fake_run)

    assert manager.load_model("owner/model", wait=True)
    assert "--yes" in captured["args"]
