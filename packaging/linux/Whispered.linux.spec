# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-directory build for Linux. Run only on Linux.

Mirrors ``packaging/windows/Whispered.windows.spec``: the whisper stack
(pywhispercpp + its bundled ``libwhisper``/``libggml`` shared objects) is
collected straight into ``dist/Whispered/`` rather than kept external, so
``main._setup_frozen_runtime()`` needs no Linux branch — the frozen
interpreter imports ``pywhispercpp`` from the package like any other dep.

Target glibc is whatever the build host ships; build inside a container
matching the oldest distribution you intend to support (the release
workflow uses a current Fedora image).
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
ASSETS = ROOT / "assets"

datas = [
    (str(ROOT / "locales"), "locales"),
    (str(ROOT / "prompts"), "prompts"),
    (str(ASSETS / "icon.png"), "assets"),
    (str(ASSETS / "covers"), "assets/covers"),
    (str(ASSETS / "fonts"), "assets/fonts"),
]
binaries = collect_dynamic_libs("pywhispercpp")
hiddenimports = [
    "qdarktheme",
    "article_generator",
    "batch_processor",
    "book_pipeline",
    "diarizer",
    "lm_studio_manager",
    "text_processor",
    "timeline_export",
    "video_cut",
    "video_edit",
    "video_input",
    *collect_submodules("pywhispercpp"),
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas + collect_data_files("pywhispercpp"),
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "PyQt5", "PySide6"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Whispered",
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Whispered",
)
