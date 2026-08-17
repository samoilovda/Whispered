"""Platform data-location tests; no real user directories are touched."""

import os
import stat

import pytest

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


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions only")
def test_data_directories_are_owner_only(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setattr(paths.platform, "system", lambda: "Linux")

    directories = (
        paths.data_dir(),
        paths.models_dir(),
        paths.logs_dir(),
        paths.output_dir(),
        paths.runtime_dir(),
    )

    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in directories)


def test_artifact_dir_scopes_same_stem_sources_by_record_id(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setattr(paths.platform, "system", lambda: "Linux")

    first = paths.artifact_dir(1, "/Users/alice/podcasts/interview.mp4")
    second = paths.artifact_dir(2, "/Users/alice/other-folder/interview.mp4")

    assert first != second
    assert first.parent == paths.output_dir()
    assert first.name.startswith("interview-")
    assert second.name.startswith("interview-")


def test_artifact_dir_sanitizes_unsafe_stem_characters(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setattr(paths.platform, "system", lambda: "Linux")

    result = paths.artifact_dir(7, "weird:name/with*chars?.mp4")

    assert paths.output_dir() in result.parents
    assert "/" not in result.name and ":" not in result.name and "*" not in result.name


def test_artifact_dir_falls_back_for_empty_stem(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    monkeypatch.setattr(paths.platform, "system", lambda: "Linux")

    result = paths.artifact_dir(3, "...")

    assert result.name == "output-3"


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
