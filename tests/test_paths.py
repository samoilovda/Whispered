"""Platform data-location tests; no real user directories are touched."""

import core.paths as paths


def _home(monkeypatch, tmp_path):
    home = tmp_path / "home с пробелом"
    monkeypatch.setattr(paths.Path, "home", staticmethod(lambda: home))
    return home


def test_windows_data_uses_local_appdata(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    (home / ".local" / "share" / "Whispered").mkdir(parents=True)
    local = tmp_path / "Local AppData"
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(paths.platform, "system", lambda: "Windows")

    assert paths.data_dir() == local / "Whispered"
    assert paths.models_dir() == local / "Whispered" / "models"
    assert paths.logs_dir() == local / "Whispered" / "logs"
    assert paths.output_dir() == local / "Whispered" / "output"


def test_windows_data_falls_back_to_local_profile(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(paths.platform, "system", lambda: "Windows")

    assert paths.data_dir() == home / "AppData" / "Local" / "Whispered"


def test_macos_keeps_preexisting_xdg_data(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    old = home / ".local" / "share" / "Whispered"
    old.mkdir(parents=True)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(paths.platform, "system", lambda: "Darwin")

    assert paths.data_dir() == old
    assert paths.models_dir() == home / "Library" / "Application Support" / "Whispered" / "models"


def test_macos_ignores_linux_legacy_directory(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    (home / ".whisper-fedora").mkdir(parents=True)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(paths.platform, "system", lambda: "Darwin")

    assert paths.data_dir() == home / "Library" / "Application Support" / "Whispered"


def test_linux_uses_xdg_data(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setattr(paths.platform, "system", lambda: "Linux")

    assert paths.data_dir() == xdg / "Whispered"
    assert paths.history_path() == xdg / "Whispered" / "history.db"


def test_resource_path_uses_source_root_when_not_frozen(monkeypatch):
    monkeypatch.delattr(paths.sys, "frozen", raising=False)
    assert paths.resource_path("locales").name == "locales"


def test_macos_bundle_path_resolves_embedded_helper_root(monkeypatch, tmp_path):
    executable = tmp_path / "Whispered.app" / "Contents" / "MacOS" / "Whispered"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.sys, "executable", str(executable))
    assert paths.macos_bundle_path() == tmp_path / "Whispered.app"
