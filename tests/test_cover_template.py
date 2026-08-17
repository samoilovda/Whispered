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
