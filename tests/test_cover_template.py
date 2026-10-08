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


class TestPathContainment:
    """R11: decor asset paths must stay inside the template root."""

    def _make_template_with_decor(self, tmp_path, path_value):
        """Helper: create a minimal template JSON with a decor layer."""
        template_dir = tmp_path / "templates"
        template_dir.mkdir()
        source = template_dir / "test.json"
        raw = {
            "id": "test",
            "canvas": {"w": 1280, "h": 720},
            "fonts": {},
            "palette": {},
            "variants": {"default": {}},
            "layouts": {
                "duo": {
                    "layers": [
                        {"type": "decor", "path": path_value, "box": [0, 0, 100, 100]},
                    ]
                }
            },
        }
        source.write_text(json.dumps(raw), encoding="utf-8")
        return raw, source

    def test_path_traversal_raises(self, tmp_path):
        raw, source = self._make_template_with_decor(tmp_path, "../../etc/passwd")
        with pytest.raises(TemplateError, match="escapes template root"):
            CoverTemplate.from_dict(raw, source)

    def test_absolute_path_raises(self, tmp_path):
        raw, source = self._make_template_with_decor(tmp_path, "/etc/passwd")
        with pytest.raises(TemplateError, match="escapes template root"):
            CoverTemplate.from_dict(raw, source)

    def test_overlong_path_raises(self, tmp_path):
        long_name = "a" * 300
        raw, source = self._make_template_with_decor(tmp_path, long_name)
        with pytest.raises(TemplateError, match="too long"):
            CoverTemplate.from_dict(raw, source)

    def test_oversize_json_raises(self, tmp_path):
        from covers.template import load_template, _MAX_TEMPLATE_BYTES
        big_file = tmp_path / "big.json"
        # Write > 1 MB of valid-looking JSON
        payload = json.dumps(
            {"id": "big", "canvas": {"w": 1, "h": 1}, "data": "x" * (_MAX_TEMPLATE_BYTES + 100)}
        )
        big_file.write_text(payload, encoding="utf-8")
        with pytest.raises(TemplateError, match="too large"):
            load_template(big_file)


@pytest.mark.parametrize("name", ["prosvet_16x9", "prosvet_9x16"])
def test_every_palette_defines_every_colour_role(name):
    # Each palette comes from one slide of the brand deck; a role missing
    # in one of them would only surface when "auto" happened to pick it.
    template = load_template(name)
    roles = set().union(*(variant.values for variant in template.variants.values()))
    for variant in template.variants.values():
        assert roles <= set(variant.values), variant.name
        for layout in template.layouts:
            template.resolve(layout, variant.name)


def test_both_formats_share_the_palettes():
    # The Shorts export reuses the variant picked for the 16:9 cover.
    assert list(load_template("prosvet_16x9").variants) == list(
        load_template("prosvet_9x16").variants
    )


def test_decor_set_layer_expands_to_the_chosen_leaves():
    template = load_template("prosvet_16x9")
    assert len(template.decor_sets) >= 5
    for set_name, leaves in template.decor_sets.items():
        layers = template.resolve("duo", "teal", set_name)
        decor = [layer for layer in layers if layer.type == "decor"]
        assert [layer.get("path") for layer in decor] == [
            leaf.get("path") for leaf in leaves
        ]
        assert not any(layer.type == "decor_set" for layer in layers)
    with pytest.raises(TemplateError, match="unknown decor set"):
        template.resolve("duo", "teal", "slide_99")


def test_decor_sets_accept_only_decor_layers(tmp_path):
    source = tmp_path / "templates" / "bad.json"
    source.parent.mkdir()
    raw = {
        "id": "bad", "canvas": {"w": 1280, "h": 720}, "fonts": {},
        "palette": {"white": "#fff"}, "variants": {"mint": {}},
        "decor_sets": {"one": [{"type": "rect", "box": [0, 0, 1, 1], "fill": "white"}]},
        "layouts": {"duo": {"layers": [{"type": "decor_set"}]}},
    }
    with pytest.raises(TemplateError, match="only decor layers"):
        CoverTemplate.from_dict(raw, source)
