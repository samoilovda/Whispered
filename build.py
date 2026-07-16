#!/usr/bin/env python3
"""
Whispered – Standalone build (macOS)

Builds dist/Whispered.app with PyInstaller, deliberately EXCLUDING the
whisper stack (pywhispercpp + its libwhisper/libggml dylibs). Those are
deployed separately to ~/Library/Application Support/Whispered/lib and
picked up at runtime by main.py's _setup_frozen_runtime(), so the native
Metal build can be updated without rebuilding the app. GGML models were
already external (~/Library/Application Support/Whispered/models).

Usage:
    .venv/bin/python build.py            # build .app + deploy whisper libs
    .venv/bin/python build.py --no-libs  # build .app only

Requirements: run with the project venv's python (it must have the same
major.minor version as the interpreter the external _pywhispercpp
extension was built for — the .so is tagged cpython-311).
"""

import argparse
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
LIB_DEPLOY_DIR = Path.home() / "Library/Application Support/Whispered/lib"

# Whisper stack pieces to deploy from site-packages. The compiled
# extension references its dylibs via @loader_path/pywhispercpp/.dylibs/,
# so the package dir and the .so must sit side by side.
# pywhispercpp's pure-python deps ride along too: the .app excludes the
# whisper stack, so PyInstaller never collects what only pywhispercpp
# imports (platformdirs/tqdm — "No module named 'platformdirs'" in the
# transcription child otherwise). requests and friends are currently
# pulled into the bundle by other imports, but the external stack must
# not depend on that staying true.
_WHISPER_GLOBS = (
    "pywhispercpp", "_pywhispercpp.*.so", "libwhisper*.dylib",
    "pywhispercpp-*.dist-info",
    "platformdirs", "platformdirs-*.dist-info",
    "tqdm", "tqdm-*.dist-info",
    "requests", "requests-*.dist-info",
    "urllib3", "urllib3-*.dist-info",
    "idna", "idna-*.dist-info",
    "certifi", "certifi-*.dist-info",
    "charset_normalizer", "charset_normalizer-*.dist-info",
)


def build_app() -> None:
    for name in ("build", "dist"):
        target = PROJECT / name
        if target.exists():
            print(f"🧹 cleaning {name}/")
            shutil.rmtree(target)
    spec = PROJECT / "Whispered.spec"
    if spec.exists():
        spec.unlink()

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name=Whispered",
        "--windowed",
        # Bundle icon (Finder/Dock) + the PNG main.py sets on the windows
        f"--icon={PROJECT / 'assets' / 'icon.icns'}",
        # Runtime data files resolved relative to the code tree
        "--add-data=locales:locales",
        "--add-data=prompts:prompts",
        "--add-data=assets/icon.png:assets",
        # Imported dynamically (theme fallback), invisible to analysis
        "--hidden-import=qdarktheme",
        # The whole point of this build: whisper stays external
        "--exclude-module=pywhispercpp",
        "--exclude-module=_pywhispercpp",
        # Bloat that is definitely not needed
        "--exclude-module=tkinter",
        "--exclude-module=pytest",
        "--exclude-module=PyQt5",
        "--exclude-module=PySide6",
        "main.py",
    ]
    print("🚀 PyInstaller:", " ".join(args[2:]))
    subprocess.run(args, cwd=PROJECT, check=True)

    app = PROJECT / "dist" / "Whispered.app"
    if not app.is_dir():
        raise SystemExit("❌ dist/Whispered.app was not produced")

    # Belt and braces: the excludes above should keep the whisper stack
    # out, but a stray hook could still pull the dylibs in — and then the
    # bundled (possibly stale) copy would shadow the external one. (Match
    # the stack's actual artifact names, not bare "whisper" — the app
    # binary itself is named Whispered.)
    leaked = [
        p for p in app.rglob("*")
        if p.name.startswith(("libwhisper", "libggml", "_pywhispercpp"))
        or p.name == "pywhispercpp"
    ]
    if leaked:
        raise SystemExit(f"❌ whisper artifacts leaked into the bundle: {leaked[:5]}")
    print(f"✅ built {app} (whisper stack verified absent)")


def deploy_whisper_libs() -> None:
    site = Path(sysconfig.get_paths()["purelib"])
    sources: list[Path] = []
    for pattern in _WHISPER_GLOBS:
        sources.extend(site.glob(pattern))
    if not any(s.name == "pywhispercpp" for s in sources):
        raise SystemExit(
            f"❌ pywhispercpp not found in {site} — run from the project venv"
        )

    LIB_DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    for src in sources:
        dst = LIB_DEPLOY_DIR / src.name
        if dst.exists():
            shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        print(f"📦 {src.name} → {dst}")
    print(f"✅ whisper libs deployed to {LIB_DEPLOY_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-libs", action="store_true",
                        help="skip deploying whisper libs to Application Support")
    opts = parser.parse_args()

    build_app()
    if not opts.no_libs:
        deploy_whisper_libs()


if __name__ == "__main__":
    main()
