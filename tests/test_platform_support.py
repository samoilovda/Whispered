from core import platform_support


def test_windows_has_no_live_system_audio(monkeypatch):
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Windows")
    assert not platform_support.supports_live_system_audio()
    assert "Windows" in platform_support.live_system_audio_unavailable_message()


def test_macos_has_live_system_audio(monkeypatch):
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Darwin")
    assert platform_support.supports_live_system_audio()
