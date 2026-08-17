from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage
import pytest

from covers.export import export
from covers.renderer import render
from covers.template import load_template


def test_renderer_is_deterministic_and_export_obeys_budget(tmp_path):
    template = load_template("prosvet_16x9")
    slots = {"title": "КАК МИФЫ ПОМОГАЮТ\nСПРАВИТЬСЯ С СОБОЙ", "names": "Роман и Денис"}
    first, _ = render(template, "duo", "mint", slots, QSize(1280, 720))
    second, _ = render(template, "duo", "mint", slots, QSize(1280, 720))
    assert first.size() == QSize(1280, 720)
    assert first.constBits().asstring(first.sizeInBytes()) == second.constBits().asstring(second.sizeInBytes())
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
