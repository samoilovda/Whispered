"""macOS Live packaging helpers without invoking PyInstaller or codesign."""

from __future__ import annotations

import plistlib

import build


def _app(tmp_path):
    app = tmp_path / "Whispered.app"
    info = app / "Contents" / "Info.plist"
    info.parent.mkdir(parents=True)
    with info.open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": "Whispered"}, handle)
    return app


def test_configure_macos_privacy_adds_live_capture_purpose_strings(tmp_path):
    app = _app(tmp_path)

    build.configure_macos_privacy(app)

    with (app / "Contents" / "Info.plist").open("rb") as handle:
        info = plistlib.load(handle)
    assert info["CFBundleIdentifier"] == "io.github.whispered"
    for key in (
        "NSMicrophoneUsageDescription",
        "NSScreenCaptureUsageDescription",
        "NSAudioCaptureUsageDescription",
    ):
        assert info[key]


def test_install_capture_helper_embeds_and_signs_nested_code(tmp_path, monkeypatch):
    app = _app(tmp_path)
    helper = tmp_path / "whispered-capture-helper"
    helper.write_bytes(b"helper")
    calls: list[list[str]] = []
    monkeypatch.setattr(build.subprocess, "run", lambda command, **_kwargs: calls.append(command))

    build.install_capture_helper(app, helper)

    destination = app / "Contents" / "Helpers" / build.HELPER_NAME
    assert destination.read_bytes() == b"helper"
    assert calls == [
        ["codesign", "--force", "--sign", "-", str(destination)],
        ["codesign", "--force", "--sign", "-", str(app)],
    ]
