"""QPainter renderer for declarative cover templates."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QPointF, QRectF, QSize
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTransform,
)

from core.paths import resource_path
from covers.template import CoverTemplate
from covers.text_layout import fit_text

_FONTS_REGISTERED = False
_FONT_WARNINGS: list[str] = []
_FONT_FAMILIES: dict[str, str] = {}


def _register_fonts(template: CoverTemplate) -> list[str]:
    global _FONTS_REGISTERED, _FONT_WARNINGS
    if _FONTS_REGISTERED:
        return list(_FONT_WARNINGS)
    _FONTS_REGISTERED = True
    for role, spec in template.fonts.items():
        filename = spec.get("file", "")
        bundled = resource_path(Path("assets/fonts") / filename)
        candidates = [
            bundled,
            Path.home() / "Library" / "Fonts" / filename,
            Path.home() / ".local" / "share" / "fonts" / filename,
        ]
        font_id = -1
        for candidate in candidates:
            if candidate.is_file():
                font_id = QFontDatabase.addApplicationFont(str(candidate))
                if font_id >= 0:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    if families:
                        _FONT_FAMILIES[role] = families[0]
                    break
        if any(candidate.is_file() for candidate in candidates) and font_id < 0:
            _FONT_WARNINGS.append(f"Не удалось загрузить шрифт {filename}")
        family = spec.get("family", "")
        if (
            family
            and family not in QFontDatabase.families()
            and role not in _FONT_FAMILIES
        ):
            _FONT_WARNINGS.append(f"Установите шрифт {family} для фирменного вида")
    return list(_FONT_WARNINGS)


def _color(value: Any) -> QColor:
    if isinstance(value, dict):
        result = QColor(value.get("color", "#000000"))
        result.setAlphaF(float(value.get("alpha", 1)))
        return result
    return QColor(str(value))


def _asset(template: CoverTemplate, category: str, name: str) -> Path:
    return template.root / category / name


def _draw_fitted(painter: QPainter, image: QImage, rect: QRectF, fit: str) -> None:
    if image.isNull():
        return
    iw, ih = image.width(), image.height()
    if fit == "contain":
        ratio = min(rect.width() / iw, rect.height() / ih)
    else:
        ratio = max(rect.width() / iw, rect.height() / ih)
    target_w, target_h = iw * ratio, ih * ratio
    target = QRectF(
        rect.center().x() - target_w / 2,
        rect.center().y() - target_h / 2,
        target_w,
        target_h,
    )
    painter.drawImage(target, image)


def _parse_path(value: str) -> QPainterPath:
    tokens = re.findall(r"[MCZ]|-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value)
    path = QPainterPath()
    i = 0
    while i < len(tokens):
        command = tokens[i]
        i += 1
        if command == "M":
            path.moveTo(float(tokens[i]), float(tokens[i + 1]))
            i += 2
        elif command == "C":
            path.cubicTo(*(float(token) for token in tokens[i : i + 6]))
            i += 6
        elif command == "Z":
            path.closeSubpath()
        else:
            raise ValueError(f"unsupported decor path command: {command}")
    return path


def _font(template: CoverTemplate, role: str, size: float) -> QFont:
    family = _FONT_FAMILIES.get(role, template.fonts.get(role, {}).get("family", ""))
    if family and family in QFontDatabase.families():
        result = QFont(family)
    else:
        result = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    result.setPixelSize(max(1, round(size)))
    return result


def render(
    template: CoverTemplate,
    layout: str,
    variant: str,
    slots: dict[str, Any],
    size: QSize | tuple[int, int],
) -> tuple[QImage, list[str]]:
    """Render a template and return the image plus non-fatal warnings."""
    if isinstance(size, tuple):
        size = QSize(*size)
    image = QImage(size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("transparent"))
    warnings = _register_fonts(template)
    painter = QPainter(image)
    painter.setRenderHints(
        QPainter.RenderHint.Antialiasing
        | QPainter.RenderHint.SmoothPixmapTransform
        | QPainter.RenderHint.TextAntialiasing
    )
    sx, sy = size.width() / template.canvas[0], size.height() / template.canvas[1]
    painter.scale(sx, sy)

    def box(layer) -> QRectF:
        return QRectF(*(float(value) for value in layer.get("box")))

    resolved_layers = template.resolve(layout, variant)
    for layer in resolved_layers:
        kind = layer.type
        if kind == "background_image":
            source = _asset(template, "backgrounds", layer.get("source"))
            bg = QImage(str(source))
            canvas = QRectF(0, 0, *template.canvas)
            _draw_fitted(painter, bg, canvas, layer.get("fit", "cover"))
        elif kind == "rect":
            painter.fillRect(box(layer), _color(layer.get("fill")))
        elif kind == "round_rect":
            rect = box(layer)
            grow_slot = layer.get("grow_with")
            if grow_slot:
                text_layer = next(
                    (
                        item
                        for item in resolved_layers
                        if item.type == "text" and item.get("slot") == grow_slot
                    ),
                    None,
                )
                if text_layer is not None:
                    px = float(text_layer.get("size", 36))
                    metrics = QFontMetricsF(
                        _font(template, text_layer.get("font", "display"), px)
                    )
                    padding = float(layer.get("grow_padding", 0)) * 2
                    desired = min(
                        template.canvas[0],
                        metrics.horizontalAdvance(str(slots.get(grow_slot, "")))
                        + padding,
                    )
                    center_x = rect.center().x()
                    rect.setLeft(center_x - desired / 2)
                    rect.setWidth(desired)
            radius = float(layer.get("radius_ratio", 0)) * min(
                rect.width(), rect.height()
            )
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            if layer.get("fill") is not None:
                painter.fillPath(path, _color(layer.get("fill")))
            stroke = layer.get("stroke")
            if stroke:
                painter.setPen(
                    QPen(_color(stroke["color"]), float(stroke.get("width", 1)))
                )
                painter.drawPath(path)
                painter.setPen(QPen())
        elif kind == "decor":
            value = _asset(template, "decor", f"{layer.get('path')}.path").read_text(
                encoding="utf-8"
            )
            path = _parse_path(value)
            bounds, rect = path.boundingRect(), box(layer)
            transform = QTransform()
            transform.translate(rect.x(), rect.y())
            if bounds.width() and bounds.height():
                transform.scale(
                    rect.width() / bounds.width(), rect.height() / bounds.height()
                )
                transform.translate(-bounds.x(), -bounds.y())
            painter.fillPath(transform.map(path), _color(layer.get("fill")))
        elif kind in {"photo", "image"}:
            # A distinct name from the `value: str` inferred a few
            # branches up (the decor-path case) — reusing `value` here
            # unified the two branches' types under mypy and flagged this
            # assignment as incompatible even though the branches never
            # actually run in the same iteration.
            photo_value = (
                slots.get(layer.get("slot")) if kind == "photo" else layer.get("source")
            )
            if photo_value:
                if isinstance(photo_value, QImage):
                    picture = photo_value
                elif isinstance(photo_value, QPixmap):
                    picture = photo_value.toImage()
                elif isinstance(photo_value, dict):
                    picture = QImage(str(photo_value.get("file", "")))
                else:
                    path = (
                        Path(str(photo_value))
                        if kind == "photo"
                        else _asset(template, "logo", str(photo_value))
                    )
                    picture = QImage(str(path))
                rect = box(layer)
                painter.save()
                radius = float(layer.get("radius_ratio", 0)) * min(
                    rect.width(), rect.height()
                )
                if radius:
                    clip = QPainterPath()
                    clip.addRoundedRect(rect, radius, radius)
                    painter.setClipPath(clip)
                _draw_fitted(
                    painter,
                    picture,
                    rect,
                    layer.get("fit", "contain" if kind == "image" else "cover"),
                )
                painter.restore()
        elif kind == "text":
            text = str(slots.get(layer.get("slot"), layer.get("value", "")))
            rect = box(layer)
            styles = layer.get("line_styles") or [{"size": layer.get("size", 36)}]
            sizes = [float(style.get("size", 36)) for style in styles]
            role = layer.get("font", "display")

            def measure(value: str, px: float) -> tuple[float, float]:
                metrics = QFontMetricsF(_font(template, role, px))
                return metrics.horizontalAdvance(value), metrics.height()

            fitted = fit_text(
                text,
                (rect.width(), rect.height()),
                sizes,
                layer.get("autofit", {"max_lines": len(text.splitlines()) or 1}),
                measure,
            )
            if fitted.warning:
                warnings.append(fitted.warning)
            total_h = sum(fitted.heights)
            y = rect.y() + (rect.height() - total_h) / 2
            painter.setPen(_color(layer.get("color", "#FFFFFF")))
            for line, px, height in zip(fitted.lines, fitted.sizes, fitted.heights):
                painter.setFont(_font(template, role, px))
                metrics = QFontMetricsF(painter.font())
                width = metrics.horizontalAdvance(line)
                if layer.get("align", "left") == "center":
                    x = rect.center().x() - width / 2
                elif layer.get("align") == "right":
                    x = rect.right() - width
                else:
                    x = rect.x()
                painter.drawText(QPointF(x, y + metrics.ascent()), line)
                y += height
    painter.end()
    return image, warnings
