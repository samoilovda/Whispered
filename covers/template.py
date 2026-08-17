"""Schema, validation and loading of declarative cover templates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.paths import data_dir, resource_path


class TemplateError(ValueError):
    """A cover template is malformed or references missing data."""


@dataclass(frozen=True)
class Variant:
    name: str
    values: dict[str, Any]


@dataclass(frozen=True)
class Layer:
    type: str
    data: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass(frozen=True)
class ResolvedLayer:
    type: str
    data: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass(frozen=True)
class Layout:
    name: str
    label: str
    slots: tuple[str, ...]
    layers: tuple[Layer, ...]


@dataclass(frozen=True)
class Slot:
    name: str
    kind: str = "text"
    required: bool = True


@dataclass
class CoverTemplate:
    id: str
    version: int
    canvas: tuple[int, int]
    fonts: dict[str, dict[str, str]]
    palette: dict[str, str]
    variants: dict[str, Variant]
    layouts: dict[str, Layout]
    root: Path
    source: Path
    slots: dict[str, Slot] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any], source: Path) -> "CoverTemplate":
        try:
            canvas = (int(raw["canvas"]["w"]), int(raw["canvas"]["h"]))
            variants = {
                name: Variant(name, dict(values))
                for name, values in raw["variants"].items()
            }
            layouts = {}
            for name, value in raw["layouts"].items():
                layouts[name] = Layout(
                    name=name,
                    label=value.get("label", name),
                    slots=tuple(value.get("slots", ())),
                    layers=tuple(
                        Layer(
                            item["type"], {k: v for k, v in item.items() if k != "type"}
                        )
                        for item in value["layers"]
                    ),
                )
            slot_defs = {
                name: Slot(name, value.get("kind", "text"), value.get("required", True))
                for name, value in raw.get("slots", {}).items()
            }
            template = cls(
                id=str(raw["id"]),
                version=int(raw.get("version", 1)),
                canvas=canvas,
                fonts=dict(raw.get("fonts", {})),
                palette=dict(raw["palette"]),
                variants=variants,
                layouts=layouts,
                root=source.parent.parent,
                source=source,
                slots=slot_defs,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TemplateError(f"Invalid template {source}: {exc}") from exc
        template.validate()
        return template

    def validate(self) -> None:
        if self.canvas[0] <= 0 or self.canvas[1] <= 0:
            raise TemplateError("canvas dimensions must be positive")
        known = {
            "background_image",
            "rect",
            "round_rect",
            "decor",
            "photo",
            "image",
            "text",
        }
        for layout in self.layouts.values():
            for layer in layout.layers:
                if layer.type not in known:
                    raise TemplateError(f"unknown layer type: {layer.type}")
                box = layer.get("box")
                if box is not None:
                    if not isinstance(box, list) or len(box) != 4:
                        raise TemplateError(
                            f"{layout.name}: box must contain four numbers"
                        )
                    x, y, w, h = (float(value) for value in box)
                    if w < 0 or h < 0:
                        raise TemplateError(
                            f"{layout.name}: box size cannot be negative"
                        )
                    cw, ch = self.canvas
                    if x + w < -cw or y + h < -ch or x > 2 * cw or y > 2 * ch:
                        raise TemplateError(
                            f"{layout.name}: box is more than one canvas outside"
                        )
                radius = layer.get("radius_ratio")
                if radius is not None and not 0 <= float(radius) <= 0.5:
                    raise TemplateError("radius_ratio must be between 0 and 0.5")
                if layer.type == "decor":
                    path = self.root / "decor" / f"{layer.get('path')}.path"
                    if not path.is_file():
                        raise TemplateError(f"missing decor path: {path.name}")
                self._validate_references(layer.data)

    def _validate_references(self, value: Any) -> None:
        if isinstance(value, str) and value.startswith("variant."):
            role = value.split(".", 1)[1]
            missing = [
                name
                for name, variant in self.variants.items()
                if role not in variant.values
            ]
            if missing:
                raise TemplateError(
                    f"unknown variant role {role!r} in: {', '.join(missing)}"
                )
        elif isinstance(value, dict):
            for child in value.values():
                self._validate_references(child)
        elif isinstance(value, list):
            for child in value:
                self._validate_references(child)

    def _resolve_value(self, value: Any, variant: Variant) -> Any:
        if isinstance(value, str):
            if value.startswith("variant."):
                return self._resolve_value(
                    variant.values[value.split(".", 1)[1]], variant
                )
            if value in self.palette:
                return self.palette[value]
            return value
        if isinstance(value, dict):
            return {
                key: self._resolve_value(child, variant) for key, child in value.items()
            }
        if isinstance(value, list):
            return [self._resolve_value(child, variant) for child in value]
        return value

    def resolve(self, layout: str, variant: str) -> list[ResolvedLayer]:
        if layout not in self.layouts:
            raise TemplateError(f"unknown layout: {layout}")
        if variant not in self.variants:
            raise TemplateError(f"unknown variant: {variant}")
        selected = self.variants[variant]
        return [
            ResolvedLayer(layer.type, self._resolve_value(layer.data, selected))
            for layer in self.layouts[layout].layers
        ]


def load_template(name: str | Path) -> CoverTemplate:
    """Load a user override first, then the bundled template."""
    candidate = Path(name)
    if candidate.suffix == ".json" and candidate.is_file():
        paths = [candidate]
    else:
        filename = candidate.name if candidate.suffix else f"{candidate.name}.json"
        paths = [
            data_dir() / "covers" / "templates" / filename,
            resource_path(Path("assets/covers/templates") / filename),
        ]
    for path in paths:
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise TemplateError(f"Could not read template {path}: {exc}") from exc
            return CoverTemplate.from_dict(raw, path)
    raise TemplateError(f"cover template not found: {name}")
