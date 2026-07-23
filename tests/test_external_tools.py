from core import external_tools


def test_bundled_windows_tool_has_priority(monkeypatch, tmp_path):
    bundled = tmp_path / "tools" / "ffmpeg.exe"
    bundled.parent.mkdir()
    bundled.write_text("binary")
    monkeypatch.setattr(external_tools.platform, "system", lambda: "Windows")
    monkeypatch.setattr(external_tools, "resource_path", lambda _path: bundled)
    monkeypatch.setattr(external_tools.shutil, "which", lambda _name: r"C:\\PATH\\ffmpeg.exe")

    assert external_tools.resolve_tool("ffmpeg") == str(bundled)


def test_path_tool_is_resolved_to_absolute(monkeypatch, tmp_path):
    tool = tmp_path / "ffprobe"
    tool.write_text("binary")
    monkeypatch.setattr(external_tools.platform, "system", lambda: "Linux")
    monkeypatch.setattr(external_tools, "resource_path", lambda _path: tmp_path / "missing")
    monkeypatch.setattr(external_tools.shutil, "which", lambda _name: str(tool))

    assert external_tools.resolve_tool("ffprobe") == str(tool.resolve())


def test_windows_install_hint(monkeypatch):
    monkeypatch.setattr(external_tools.platform, "system", lambda: "Windows")
    assert "PATH" in external_tools.ffmpeg_install_hint()
