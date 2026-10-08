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


def _fill_color(node: ET.Element) -> str | None:
    """The shape's own solid fill as ``#RRGGBB`` (``None`` for none/scheme)."""
    color = node.find("p:spPr/a:solidFill/a:srgbClr", NS)
    return f"#{color.get('val', '').upper()}" if color is not None else None


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
                layer = {"type": "decor", "path": name, "box": box, "fill": "variant.decor"}
                color = _fill_color(node)
                if color:
                    layer["source_color"] = color
                layers.append(layer)
            elif tag == "pic":
                layers.append({"type": "image", "box": box, "source": "TODO"})
            else:
                layers.append({"type": "rect", "box": box, "fill": "#FFFFFF"})

    visit(tree)
    return layers


def decor_set(layers: list[dict]) -> list[dict]:
    """Keep a slide's decor layers as a reusable set. The slide's dominant
    leaf colour becomes ``variant.decor`` and any other colour
    ``variant.decor_alt`` — several slides alternate two colours (mint and
    orange leaves on teal), and every palette defines both roles."""
    decor = [dict(layer) for layer in layers if layer["type"] == "decor"]
    colors = [layer.get("source_color") for layer in decor]
    main_color = max(set(colors), key=colors.count) if colors else None
    for layer in decor:
        color = layer.pop("source_color", None)
        layer["fill"] = (
            "variant.decor" if color == main_color else "variant.decor_alt"
        )
    return decor


def write_decor_sets(pptx: Path, slides: list[int], out: Path) -> None:
    """Add every distinct leaf arrangement among *slides* to ``decor_sets``
    of the template at *out*; layouts pick one through a ``decor_set``
    layer, so covers vary without hand-placing each leaf."""
    result = json.loads(out.read_text(encoding="utf-8"))
    sets: dict[str, list[dict]] = {}
    seen: set[str] = set()
    with zipfile.ZipFile(pptx) as archive:
        for slide in slides:
            layers = decor_set(extract_slide(archive, slide, out.parent.parent / "decor"))
            key = json.dumps(layers, sort_keys=True)
            if layers and key not in seen:
                seen.add(key)
                sets[f"slide_{slide:02d}"] = layers
    result["decor_sets"] = sets
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(sets)} decor sets to {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--slide", type=int)
    parser.add_argument("--layout")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--decor-sets", metavar="SLIDES",
        help="comma-separated slide numbers whose leaves become decor_sets",
    )
    args = parser.parse_args()
    if args.decor_sets:
        write_decor_sets(
            args.pptx, [int(n) for n in args.decor_sets.split(",")], args.out
        )
        return
    if args.slide is None or not args.layout:
        parser.error("--slide and --layout are required without --decor-sets")
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
