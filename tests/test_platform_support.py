from core import platform_support


def test_windows_has_no_live_system_audio(monkeypatch):
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Windows")
    assert not platform_support.supports_live_system_audio()
    assert "Windows" in platform_support.live_system_audio_unavailable_message()


def test_macos_has_live_system_audio(monkeypatch):
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Darwin")
    assert platform_support.supports_live_system_audio()


def test_linux_has_no_live_system_audio(monkeypatch):
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Linux")
    assert not platform_support.supports_live_system_audio()
    assert "Linux" in platform_support.live_system_audio_unavailable_message()


def test_reveal_command_per_platform(monkeypatch):
    from core import platform_support

    monkeypatch.setattr(platform_support.platform, "system", lambda: "Darwin")
    assert platform_support.reveal_command("/a b/v.mp4") == ["open", "-R", "/a b/v.mp4"]
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Windows")
    assert platform_support.reveal_command("C:\\v.mp4") == ["explorer", "/select,C:\\v.mp4"]
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Linux")
    assert platform_support.reveal_command("/v.mp4") is None


def test_reveal_in_file_manager_runs_argv_without_a_shell(monkeypatch):
    from core import platform_support

    monkeypatch.setattr(platform_support.platform, "system", lambda: "Darwin")
    calls = []
    monkeypatch.setattr(platform_support.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)))
    assert platform_support.reveal_in_file_manager("/v.mp4") is True
    assert calls == [(["open", "-R", "/v.mp4"], {})]


def test_reveal_in_file_manager_reports_failure(monkeypatch):
    from core import platform_support

    monkeypatch.setattr(platform_support.platform, "system", lambda: "Linux")
    assert platform_support.reveal_in_file_manager("/v.mp4") is False
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Darwin")

    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(platform_support.subprocess, "Popen", boom)
    assert platform_support.reveal_in_file_manager("/v.mp4") is False
