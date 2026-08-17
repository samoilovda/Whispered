#!/usr/bin/env python3
"""Extract reusable cover geometry from a PowerPoint presentation.

The CLI intentionally uses only the standard library. It emits a diagnostic
layout that can be refined by hand; repeated runs preserve existing layouts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

EMU_W, EMU_H = 16_256_000, 9_144_000
NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}
PALETTE = {
    "yellow": "#F9B913",
    "sand": "#CCB999",
    "orange": "#EE7227",
    "mint": "#B6DCCD",
    "teal": "#1B8E88",
    "brown": "#726858",
}


def emu_to_px(value: int, axis: str = "x") -> float:
    return value / (EMU_W if axis == "x" else EMU_H) * (1280 if axis == "x" else 720)


def snap_color(value: str) -> str:
    color = value.lstrip("#").upper()
    if len(color) != 6:
        return f"#{color}"
    rgb = tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))
    for name, candidate in PALETTE.items():
        target = tuple(int(candidate[i : i + 2], 16) for i in (1, 3, 5))
        if max(abs(a - b) for a, b in zip(rgb, target)) <= 3:
            if f"#{color}" != candidate:
                logging.warning("snapped #%s to %s (%s)", color, candidate, name)
            return name
    return f"#{color}"


def custom_geometry(node: ET.Element) -> str:
    path = node.find(".//a:custGeom/a:pathLst/a:path", NS)
    if path is None:
        raise ValueError("custGeom has no path")
    width, height = float(path.get("w", "1")), float(path.get("h", "1"))
    commands: list[str] = []
    for command in path:
        tag = command.tag.rsplit("}", 1)[-1]
        points = command.findall("a:pt", NS)
        if tag == "moveTo" and len(points) == 1:
            commands.append(
                f"M {float(points[0].get('x')) / width:g} {float(points[0].get('y')) / height:g}"
            )
        elif tag == "cubicBezTo" and len(points) == 3:
            coords = " ".join(
                f"{float(point.get(axis)) / (width if axis == 'x' else height):g}"
                for point in points
                for axis in ("x", "y")
            )
            commands.append(f"C {coords}")
        elif tag == "close":
            commands.append("Z")
        else:
            raise ValueError(f"unsupported custGeom command: {tag}")
    return " ".join(commands)


def _transform(node: ET.Element, mapping=None) -> list[float] | None:
    xfrm = node.find(".//a:xfrm", NS)
    if xfrm is None:
        return None
    off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
    if off is None or ext is None:
        return None
    x, y, w, h = (
        int(off.get("x", "0")),
        int(off.get("y", "0")),
        int(ext.get("cx", "0")),
        int(ext.get("cy", "0")),
    )
    if mapping is not None:
        off_x, off_y, scale_x, scale_y, child_x, child_y = mapping
        x = off_x + (x - child_x) * scale_x
        y = off_y + (y - child_y) * scale_y
        w *= scale_x
        h *= scale_y
    return [
        round(emu_to_px(x, "x"), 2),
        round(emu_to_px(y, "y"), 2),
        round(emu_to_px(w, "x"), 2),
        round(emu_to_px(h, "y"), 2),
    ]


def _group_mapping(node: ET.Element, parent=None):
    xfrm = node.find("p:grpSpPr/a:xfrm", NS)
    if xfrm is None:
        return parent
    off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
    child_off, child_ext = xfrm.find("a:chOff", NS), xfrm.find("a:chExt", NS)
    if off is None or ext is None or child_off is None or child_ext is None:
        return parent
    ox, oy = float(off.get("x", "0")), float(off.get("y", "0"))
    sx = float(ext.get("cx", "1")) / max(1.0, float(child_ext.get("cx", "1")))
    sy = float(ext.get("cy", "1")) / max(1.0, float(child_ext.get("cy", "1")))
    cx, cy = float(child_off.get("x", "0")), float(child_off.get("y", "0"))
    if parent is not None:
        pox, poy, psx, psy, pcx, pcy = parent
        ox = pox + (ox - pcx) * psx
        oy = poy + (oy - pcy) * psy
        sx *= psx
        sy *= psy
    return ox, oy, sx, sy, cx, cy


def extract_slide(archive: zipfile.ZipFile, slide: int, decor_dir: Path) -> list[dict]:
    root = ET.fromstring(archive.read(f"ppt/slides/slide{slide}.xml"))
    layers: list[dict] = []
    tree = root.find(".//p:spTree", NS)
    if tree is None:
        return layers

    def visit(container: ET.Element, mapping=None) -> None:
        for node in container:
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "grpSp":
                visit(node, _group_mapping(node, mapping))
                continue
            if tag not in {"sp", "pic"}:
                continue
            box = _transform(node, mapping)
            if not box:
                continue
            x, y, w, h = box
            if x + w < 0 or y + h < 0 or x > 1280 or y > 720:
                continue
            geometry = node.find(".//a:custGeom", NS)
            if geometry is not None:
                value = custom_geometry(node)
                # The digest only creates a stable asset filename; it is not
                # used for authentication or integrity verification.
                digest = hashlib.sha1(
                    value.encode(), usedforsecurity=False
                ).hexdigest()[:10]
                name = f"leaf_{digest}"
                decor_dir.mkdir(parents=True, exist_ok=True)
                (decor_dir / f"{name}.path").write_text(value + "\n", encoding="utf-8")
                layers.append(
                    {"type": "decor", "path": name, "box": box, "fill": "variant.decor"}
                )
            elif tag == "pic":
                layers.append({"type": "image", "box": box, "source": "TODO"})
            else:
                layers.append({"type": "rect", "box": box, "fill": "#FFFFFF"})

    visit(tree)
    return layers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--slide", type=int, required=True)
    parser.add_argument("--layout", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        result = json.loads(args.out.read_text(encoding="utf-8"))
    else:
        result = {
            "id": args.out.stem,
            "version": 1,
            "canvas": {"w": 1280, "h": 720},
            "fonts": {},
            "palette": {**PALETTE, "white": "#FFFFFF"},
            "variants": {"mint": {"decor": "mint"}},
            "layouts": {},
        }
    with zipfile.ZipFile(args.pptx) as archive:
        layers = extract_slide(archive, args.slide, args.out.parent.parent / "decor")
    result["layouts"][args.layout] = {
        "label": args.layout,
        "slots": [],
        "layers": layers,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {args.layout}: {len(layers)} layers to {args.out}")


if __name__ == "__main__":
    main()
