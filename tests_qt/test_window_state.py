"""The window comes back where the user left it: geometry and the
Library's width survive a restart, and a resize no longer snaps the
Library back to its default width."""

from __future__ import annotations

from PyQt6.QtCore import QByteArray


def test_geometry_and_library_width_are_remembered(monkeypatch, process_events):
    from config import get_config
    from ui.main_window import MainWindow

    monkeypatch.delenv("WHISPERED_UI_GALLERY", raising=False)
    monkeypatch.setattr("ui.main_window.save_config", lambda: True)
    cfg = get_config()
    monkeypatch.setattr(cfg, "window_geometry", "")
    monkeypatch.setattr(cfg, "library_width", 0)

    window = MainWindow()
    window.resize(1300, 820)
    window.show()
    process_events()
    shell = window.workspace_shell
    shell.splitter.setSizes([340, 960])
    shell._on_splitter_moved(340, 1)
    window.resize(1250, 800)  # used to reset the Library to 280px
    process_events()
    assert shell.splitter.sizes()[0] == 340

    window.close()
    process_events()
    assert cfg.window_geometry
    assert cfg.library_width == 340

    # The offscreen screen is 800x600 and Qt clamps a restored geometry
    # to the screen, so check what is handed to restoreGeometry().
    restored = []
    monkeypatch.setattr(
        MainWindow, "restoreGeometry", lambda self, data: restored.append(bytes(data)) or True,
    )
    again = MainWindow()
    again.show()
    process_events()
    assert restored and restored[0] == bytes(QByteArray.fromBase64(cfg.window_geometry.encode()))
    assert again.workspace_shell.library_width() == 340
    again.close()
    process_events()
