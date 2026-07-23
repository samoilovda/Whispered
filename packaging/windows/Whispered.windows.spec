# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-directory build for Windows. Run only on Windows."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).resolve().parents[1]
ASSETS = ROOT / "assets"

datas = [
    (str(ROOT / "locales"), "locales"),
    (str(ROOT / "prompts"), "prompts"),
    (str(ASSETS / "icon.png"), "assets"),
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
    icon=str(ASSETS / "icon.ico"),
    version=str(Path(SPECPATH) / "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Whispered",
)
