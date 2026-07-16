#!/usr/bin/env python3
"""
Whispered – icon generation

Renders assets/icon.svg into assets/icon.icns (macOS bundle icon) and
assets/icon.png (1024px, used for the runtime window/Dock icon and by the
Linux packaging). Run with the project venv's python — it needs PyQt6's
SVG renderer:

    .venv/bin/python assets/make_icon.py

The .icns step shells out to iconutil, so it only works on macOS; the PNG
is written on every platform.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent
SVG = ASSETS / "icon.svg"
PNG = ASSETS / "icon.png"
ICNS = ASSETS / "icon.icns"

# (pixel size, iconset filenames) — the set iconutil expects for a
# complete .icns, covering 1x and 2x for every slot.
_ICONSET = {
    16: ["icon_16x16.png"],
    32: ["icon_16x16@2x.png", "icon_32x32.png"],
    64: ["icon_32x32@2x.png"],
    128: ["icon_128x128.png"],
    256: ["icon_128x128@2x.png", "icon_256x256.png"],
    512: ["icon_256x256@2x.png", "icon_512x512.png"],
    1024: ["icon_512x512@2x.png"],
}


def render(renderer: QSvgRenderer, size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter)
    painter.end()
    return image


def main() -> None:
    if not SVG.is_file():
        raise SystemExit(f"❌ missing {SVG}")

    # QImage/QPainter need a QGuiApplication even for offscreen rendering.
    QGuiApplication(sys.argv)
    renderer = QSvgRenderer(QByteArray(SVG.read_bytes()))
    if not renderer.isValid():
        raise SystemExit(f"❌ {SVG} is not valid SVG")

    render(renderer, 1024).save(str(PNG))
    print(f"🎨 {PNG.name}")

    if sys.platform != "darwin" or not shutil.which("iconutil"):
        print("ℹ️  iconutil unavailable — skipping .icns")
        return

    iconset = ASSETS / "icon.iconset"
    if iconset.exists():
        shutil.rmtree(iconset)
    iconset.mkdir()
    for size, names in _ICONSET.items():
        image = render(renderer, size)
        for name in names:
            image.save(str(iconset / name))
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(ICNS)], check=True
    )
    shutil.rmtree(iconset)
    print(f"🎨 {ICNS.name} ({ICNS.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
