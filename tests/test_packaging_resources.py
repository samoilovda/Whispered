"""Static contracts for platform packagers and runtime resources."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_spec_bundles_cover_resources():
    spec = (ROOT / "packaging/windows/Whispered.windows.spec").read_text(
        encoding="utf-8"
    )
    assert 'ASSETS / "covers"' in spec
    assert 'ASSETS / "fonts"' in spec


def test_appimage_manifest_covers_current_import_graph_and_resources():
    script = (ROOT / "appimage/build-appimage.sh").read_text(encoding="utf-8")
    for filename in (
        "book_pipeline.py",
        "timeline_export.py",
        "video_cut.py",
        "video_edit.py",
        "video_input.py",
    ):
        assert f'$PROJECT_DIR/{filename}' in script
    for directory in ("ui", "core", "covers", "locales", "prompts", "assets"):
        assert f'$PROJECT_DIR/{directory}' in script


def test_appimage_desktop_identity_matches_launcher_and_icon():
    script = (ROOT / "appimage/build-appimage.sh").read_text(encoding="utf-8")
    desktop = (ROOT / "appimage/whispered.desktop").read_text(encoding="utf-8")
    assert "Exec=whispered" in desktop
    assert "Icon=whispered" in desktop
    assert '$APPDIR/usr/bin/whispered' in script
    assert "apps/whispered.png" in script
