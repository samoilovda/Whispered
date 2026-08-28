#!/usr/bin/env bash
# Whispered - Linux packaging (PyInstaller one-directory + tarball).
#
# Mirrors packaging/windows/build-windows.ps1. Produces:
#   dist/Whispered/                  - the runnable one-dir tree
#   dist/Whispered-linux-x86_64.tar.gz
#
# Build on the oldest glibc you intend to support (a container matching
# the target distribution). Fedora system packages needed:
#   sudo dnf install -y python3.11 python3.11-devel gcc gcc-c++ cmake make \
#       libxkbcommon-x11 mesa-libEGL mesa-libGL dbus-libs
# Runtime also needs ffmpeg (dnf install ffmpeg-free) and, for the xcb Qt
# platform, xcb-util-cursor.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON="${PYTHON:-python3}"
ARCH="$(uname -m)"
DIST_NAME="Whispered-linux-${ARCH}"

echo "==> Installing build dependencies"
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements-build-linux.txt

echo "==> Running PyInstaller"
rm -rf dist build/linux
"$PYTHON" -m PyInstaller --noconfirm --clean \
    --distpath dist --workpath build/linux \
    packaging/linux/Whispered.linux.spec

echo "==> Frozen smoke test"
QT_QPA_PLATFORM=offscreen ./dist/Whispered/Whispered --smoke-test

echo "==> Staging desktop file and icon"
install -Dm644 packaging/linux/whispered.desktop \
    "dist/Whispered/share/applications/whispered.desktop"
install -Dm644 assets/icon.png \
    "dist/Whispered/share/icons/hicolor/512x512/apps/whispered.png"

echo "==> Creating tarball"
tar -C dist -czf "dist/${DIST_NAME}.tar.gz" Whispered
sha256sum "dist/${DIST_NAME}.tar.gz"

echo "==> Done: dist/${DIST_NAME}.tar.gz"
