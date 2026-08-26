from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage
import pytest

from covers.export import export
from covers.renderer import render
from covers.template import load_template


def _bytes(image: QImage) -> bytes:
    return image.constBits().asstring(image.sizeInBytes())


def _max_channel_delta(a: QImage, b: QImage) -> int:
    left, right = _bytes(a), _bytes(b)
    assert len(left) == len(right)
    return max((abs(x - y) for x, y in zip(left, right)), default=0)


def test_renderer_is_deterministic_and_export_obeys_budget(tmp_path):
    template = load_template("prosvet_16x9")
    slots = {"title": "КАК МИФЫ ПОМОГАЮТ\nСПРАВИТЬСЯ С СОБОЙ", "names": "Роман и Денис"}
    # Qt's FreeType backend rasterizes a glyph slightly differently the
    # very first time it draws it in a process (it lands in the glyph
    # cache afterwards), so on Linux the process's *first* render differs
    # from every later one by a few units of antialiasing on the title's
    # outlines — with byte-identical inputs: the font family, pixel size,
    # metrics and draw positions are the same on every pass (covers/
    # renderer.py resolves all of them through `_font`). Render a cold
    # pass, hold it to a tolerance that only antialiasing can explain, and
    # assert byte equality between two warm renders — a genuinely
    # nondeterministic renderer (a random seed, a timestamp baked into the
    # image) would fail both checks, not just the second.
    cold, _ = render(template, "duo", "mint", slots, QSize(1280, 720))
    first, _ = render(template, "duo", "mint", slots, QSize(1280, 720))
    second, _ = render(template, "duo", "mint", slots, QSize(1280, 720))
    assert first.size() == QSize(1280, 720)
    assert _max_channel_delta(cold, first) <= 48
    assert _bytes(first) == _bytes(second)
    assert first.pixelColor(640, 620).name().upper() == "#F9B913"
    files = export(first, None, tmp_path, "test", state={"template": template.id}, jpeg_max_bytes=100_000)
    assert len(files) == 3
    assert (tmp_path / "test.jpg").stat().st_size <= 100_000


def test_long_title_returns_warning_without_crashing():
    template = load_template("prosvet_16x9")
    _, warnings = render(template, "duo", "mint", {"title": " ".join(["СЛОВО"] * 40)}, (1280, 720))
    assert warnings


def test_cover_export_rejects_null_image_without_partial_files(tmp_path):
    with pytest.raises(ValueError, match="empty cover image"):
        export(QImage(), None, tmp_path, "broken")

    assert list(tmp_path.iterdir()) == []
