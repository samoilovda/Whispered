"""Declarative cover schema regression tests."""

import json

import pytest

from covers.template import CoverTemplate, TemplateError, load_template


def test_bundled_template_loads_and_resolves_variant_roles():
    template = load_template("prosvet_16x9")
    layers = template.resolve("duo", "mint")
    assert template.canvas == (1280, 720)
    assert layers[1].get("fill") == {"color": "#FFFFFF", "alpha": 0.8}
    assert any(layer.get("fill") == "#B6DCCD" for layer in layers)


@pytest.mark.parametrize(
    "change, message",
    [
        ({"type": "mystery", "box": [0, 0, 1, 1]}, "unknown layer type"),
        ({"type": "rect", "box": [0, 0, -1, 1], "fill": "white"}, "box size"),
        ({"type": "round_rect", "box": [0, 0, 1, 1], "radius_ratio": 0.6, "fill": "white"}, "radius_ratio"),
        ({"type": "rect", "box": [3000, 0, 1, 1], "fill": "white"}, "outside"),
        ({"type": "rect", "box": [0, 0, 1], "fill": "white"}, "four numbers"),
        ({"type": "rect", "box": [0, 0, 1, 1], "fill": "variant.missing"}, "variant role"),
    ],
)
def test_validation_errors_are_actionable(tmp_path, change, message):
    source = tmp_path / "templates" / "bad.json"
    source.parent.mkdir()
    raw = {
        "id": "bad", "canvas": {"w": 1280, "h": 720}, "fonts": {},
        "palette": {"white": "#fff"}, "variants": {"mint": {}},
        "layouts": {"duo": {"layers": [change]}},
    }
    source.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(TemplateError, match=message):
        CoverTemplate.from_dict(raw, source)
