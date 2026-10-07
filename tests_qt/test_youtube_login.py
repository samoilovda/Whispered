"""YouTube account connection: the login worker's one-terminal-signal
contract and the Settings > AI account block. Nothing here reaches Google
or the real keyring."""

from __future__ import annotations

import json

import pytest

from core import youtube_oauth


@pytest.fixture(autouse=True)
def _no_real_keyring(monkeypatch):
    store = {}
    monkeypatch.setattr(youtube_oauth.secrets_store, "set_secret", lambda n, v: store.update({n: v}) or True)
    monkeypatch.setattr(youtube_oauth.secrets_store, "get_secret", lambda n: store.get(n))
    monkeypatch.setattr(youtube_oauth.secrets_store, "delete_secret", lambda n: store.pop(n, None))
    monkeypatch.setattr(youtube_oauth, "_memory_refresh_token", None)
    return store


def _run(worker, process_events):
    seen = []
    worker.connected.connect(lambda t: seen.append(("connected", t)))
    worker.cancelled.connect(lambda: seen.append(("cancelled",)))
    worker.failed.connect(lambda m: seen.append(("failed", m)))
    worker.start()
    assert worker.wait(5000)
    process_events()
    return seen


def test_worker_reports_the_channel_title(monkeypatch, process_events):
    from core.youtube_oauth_worker import YouTubeLoginWorker

    monkeypatch.setattr(youtube_oauth, "authorize", lambda *a, **k: "rt")
    monkeypatch.setattr(youtube_oauth.TokenProvider, "access_token", lambda self: "at")
    monkeypatch.setattr(youtube_oauth, "fetch_channel_title", lambda token, **k: "My channel")
    assert _run(YouTubeLoginWorker("cid", "csec"), process_events) == [("connected", "My channel")]


@pytest.mark.parametrize("error, expected", [
    (youtube_oauth.YouTubeAuthCancelled("x"), ("cancelled",)),
    (youtube_oauth.YouTubeAuthError("boom"), ("failed", "boom")),
])
def test_worker_maps_auth_errors_to_one_terminal_signal(monkeypatch, process_events, error, expected):
    from core.youtube_oauth_worker import YouTubeLoginWorker

    def raise_it(*a, **k):
        raise error

    monkeypatch.setattr(youtube_oauth, "authorize", raise_it)
    assert _run(YouTubeLoginWorker("cid", "csec"), process_events) == [expected]


def test_worker_turns_unexpected_errors_into_failed(monkeypatch, process_events):
    from core.youtube_oauth_worker import YouTubeLoginWorker

    def raise_it(*a, **k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(youtube_oauth, "authorize", raise_it)
    assert _run(YouTubeLoginWorker("cid", "csec"), process_events) == [("failed", "kaput")]


@pytest.fixture
def dialog(monkeypatch, tmp_path, process_events):
    import config
    from ui.settings_dialog import SettingsDialog

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "_config", config.Config())
    dlg = SettingsDialog()
    yield dlg
    dlg.close()
    process_events()


def test_account_block_walks_through_its_states(dialog, monkeypatch, tmp_path):
    assert not dialog._yt_connect_btn.isEnabled() and not dialog._yt_disconnect_btn.isEnabled()

    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}))
    monkeypatch.setattr(
        "ui.settings_dialog.QFileDialog.getOpenFileName", lambda *a, **k: (str(path), ""))
    dialog._import_yt_client()
    assert dialog._cfg.yt_oauth_client_id == "cid" and dialog._cfg.yt_oauth_client_secret == "csec"
    assert dialog._yt_connect_btn.isEnabled() and not dialog._yt_disconnect_btn.isEnabled()

    youtube_oauth.store_refresh_token("rt")
    dialog._on_yt_connected("My channel")
    assert "My channel" in dialog._yt_status.text()
    assert dialog._yt_disconnect_btn.isEnabled() and not dialog._yt_connect_btn.isEnabled()

    monkeypatch.setattr(youtube_oauth, "disconnect", lambda **k: youtube_oauth.clear_refresh_token())
    dialog._disconnect_youtube()
    assert dialog._cfg.yt_channel_title == ""
    assert dialog._yt_connect_btn.isEnabled() and not dialog._yt_disconnect_btn.isEnabled()


def test_importing_a_broken_client_file_keeps_the_old_state(dialog, monkeypatch, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    monkeypatch.setattr(
        "ui.settings_dialog.QFileDialog.getOpenFileName", lambda *a, **k: (str(bad), ""))
    warned = []
    monkeypatch.setattr("ui.settings_dialog.QMessageBox.warning", lambda *a, **k: warned.append(a[2]))
    dialog._import_yt_client()
    assert warned and dialog._cfg.yt_oauth_client_id == ""


def test_connect_starts_the_worker_and_failure_is_reported(dialog, monkeypatch, process_events):
    dialog._cfg.yt_oauth_client_id = "cid"
    dialog._cfg.yt_oauth_client_secret = "csec"
    dialog._refresh_yt_status()

    def refuse(*a, **k):
        raise youtube_oauth.YouTubeAuthError("nope")

    monkeypatch.setattr(youtube_oauth, "authorize", refuse)
    warned = []
    monkeypatch.setattr("ui.settings_dialog.QMessageBox.warning", lambda *a, **k: warned.append(a[2]))
    dialog._connect_youtube()
    worker = dialog._yt_login
    assert worker is not None and not dialog._yt_connect_btn.isEnabled()
    assert worker.wait(5000)
    process_events()
    assert dialog._yt_login is None and warned and "nope" in warned[0]


def test_closing_the_dialog_retires_a_running_login(dialog, monkeypatch):
    dialog._cfg.yt_oauth_client_id = "cid"
    dialog._cfg.yt_oauth_client_secret = "csec"

    def wait_for_cancel(client_id, client_secret, *, open_browser, is_cancelled, **k):
        import time
        while not is_cancelled():
            time.sleep(0.01)
        raise youtube_oauth.YouTubeAuthCancelled("cancelled")

    monkeypatch.setattr(youtube_oauth, "authorize", wait_for_cancel)
    dialog._connect_youtube()
    worker = dialog._yt_login
    dialog.reject()
    assert dialog._yt_login is None
    assert worker.wait(5000)
