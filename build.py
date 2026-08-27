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
    .venv/bin/python build.py                       # build .app + deploy whisper libs (dev)
    .venv/bin/python build.py --no-libs             # build .app only
    .venv/bin/python build.py --bundle-libs --no-libs  # self-contained release .app

Requirements: run with the project venv's python (it must have the same
major.minor version as the interpreter the external _pywhispercpp
extension was built for — the .so is tagged cpython-311).
"""

import argparse
import plistlib
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
LIB_DEPLOY_DIR = Path.home() / "Library/Application Support/Whispered/lib"
HELPER_PROJECT = PROJECT / "native" / "system_capture_helper"
HELPER_NAME = "whispered-capture-helper"
_BUNDLE_IDENTIFIER = "io.github.whispered"
# --bundle-libs stashes the whisper stack here, inside the .app, for a
# self-contained release. main._setup_frozen_runtime() falls back to this
# path when the external LIB_DEPLOY_DIR is absent (i.e. on any machine
# that never ran a dev build). Keep the name in step with main.py.
BUNDLED_RUNTIME_DIRNAME = "whisper-runtime"

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


def build_app(bundle_libs: bool = False) -> None:
    helper = build_capture_helper()
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
        f"--osx-bundle-identifier={_BUNDLE_IDENTIFIER}",
        # Bundle icon (Finder/Dock) + the PNG main.py sets on the windows
        f"--icon={PROJECT / 'assets' / 'icon.icns'}",
        # Runtime data files resolved relative to the code tree
        "--add-data=locales:locales",
        "--add-data=prompts:prompts",
        "--add-data=assets/icon.png:assets",
        "--add-data=assets/covers:assets/covers",
        "--add-data=assets/fonts:assets/fonts",
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

    configure_macos_privacy(app)

    # Belt and braces: the excludes above should keep the whisper stack out
    # of the PyInstaller output, but a stray hook could still pull the
    # dylibs in — and then that (possibly stale) copy would shadow the
    # external one. Run this before an intentional --bundle-libs so the
    # check only ever sees what PyInstaller itself collected. (Match the
    # stack's actual artifact names, not bare "whisper" — the app binary
    # itself is named Whispered.)
    leaked = [
        p for p in app.rglob("*")
        if p.name.startswith(("libwhisper", "libggml", "_pywhispercpp"))
        or p.name == "pywhispercpp"
    ]
    if leaked:
        raise SystemExit(f"❌ whisper artifacts leaked into the bundle: {leaked[:5]}")

    if bundle_libs:
        bundle_whisper_libs(app)
        print(f"✅ built {app} (whisper stack bundled for distribution)")
    else:
        print(f"✅ built {app} (whisper stack kept external)")

    # Helper goes in last: it re-seals the outer bundle, so anything added
    # to the .app (privacy strings, --bundle-libs) must already be in place.
    install_capture_helper(app, helper)


def build_capture_helper() -> Path:
    """Compile the native ScreenCaptureKit executable used by Live."""
    if not HELPER_PROJECT.is_dir():
        raise SystemExit(f"❌ capture helper project is missing: {HELPER_PROJECT}")
    try:
        subprocess.run(["swift", "build", "-c", "release"], cwd=HELPER_PROJECT, check=True)
    except FileNotFoundError as exc:
        raise SystemExit("❌ Swift is required to build Live system-audio capture") from exc
    helper = HELPER_PROJECT / ".build" / "release" / HELPER_NAME
    if not helper.is_file():
        raise SystemExit(f"❌ capture helper was not produced: {helper}")
    return helper


def install_capture_helper(app: Path, helper: Path) -> None:
    """Embed and ad-hoc sign the helper before sealing the outer app bundle."""
    destination = app / "Contents" / "Helpers" / HELPER_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(helper, destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    # PyInstaller has already signed the outer bundle. Adding code changes its
    # seal, so sign the nested executable first and the app last. Release
    # distribution can replace '-' with a Developer ID in a later signing step.
    subprocess.run(["codesign", "--force", "--sign", "-", str(destination)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)


def configure_macos_privacy(app: Path) -> None:
    """Add purpose strings required before macOS grants live capture access."""
    info_path = app / "Contents" / "Info.plist"
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    info.update({
        "CFBundleIdentifier": _BUNDLE_IDENTIFIER,
        "NSMicrophoneUsageDescription": (
            "Whispered uses your microphone to transcribe a live meeting locally."
        ),
        "NSScreenCaptureUsageDescription": (
            "Whispered captures selected meeting audio to transcribe it locally."
        ),
        "NSAudioCaptureUsageDescription": (
            "Whispered captures selected meeting audio to transcribe it locally."
        ),
    })
    with info_path.open("wb") as handle:
        plistlib.dump(info, handle)


def _collect_whisper_sources(site: Path | None = None) -> list[Path]:
    site = site or Path(sysconfig.get_paths()["purelib"])
    sources: list[Path] = []
    for pattern in _WHISPER_GLOBS:
        sources.extend(site.glob(pattern))
    if not any(s.name == "pywhispercpp" for s in sources):
        raise SystemExit(
            f"❌ pywhispercpp not found in {site} — install it into this "
            "environment first (pip install -r requirements.lock)"
        )
    return sources


def _mirror_into(sources: list[Path], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for src in sources:
        dst = target / src.name
        if dst.exists():
            shutil.rmtree(dst) if dst.is_dir() else dst.unlink()
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
        print(f"📦 {src.name} → {dst}")


def deploy_whisper_libs() -> None:
    _mirror_into(_collect_whisper_sources(), LIB_DEPLOY_DIR)
    print(f"✅ whisper libs deployed to {LIB_DEPLOY_DIR}")


def bundle_whisper_libs(app: Path, site: Path | None = None) -> None:
    """Copy the whisper stack into the .app for a self-contained release.

    The dev workflow keeps whisper external (``deploy_whisper_libs``) so a
    Metal rebuild does not need a fresh .app. A downloaded release has no
    such external directory on the user's machine, so ``--bundle-libs``
    stashes the same files under
    ``Contents/Resources/<BUNDLED_RUNTIME_DIRNAME>`` and
    ``main._setup_frozen_runtime()`` falls back to that path.
    """
    target = app / "Contents" / "Resources" / BUNDLED_RUNTIME_DIRNAME
    _mirror_into(_collect_whisper_sources(site), target)
    print(f"✅ whisper stack bundled into {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-libs", action="store_true",
                        help="skip deploying whisper libs to Application Support")
    parser.add_argument("--bundle-libs", action="store_true",
                        help="copy the whisper stack into the .app itself "
                             "(self-contained release build)")
    opts = parser.parse_args()

    build_app(bundle_libs=opts.bundle_libs)
    if not opts.no_libs:
        deploy_whisper_libs()


if __name__ == "__main__":
    main()
