"""Text fitting helpers used by the QPainter renderer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextLayout:
    lines: tuple[str, ...]
    sizes: tuple[float, ...]
    heights: tuple[float, ...]
    widths: tuple[float, ...]
    warning: str = ""


def _wrap_words(text: str, measure, size: float, max_width: float) -> list[str]:
    result: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            result.append("")
            continue
        line = words[0]
        for word in words[1:]:
            proposed = f"{line} {word}"
            if measure(proposed, size)[0] <= max_width:
                line = proposed
            else:
                result.append(line)
                line = word
        result.append(line)
    return result


def fit_text(
    text: str, box: tuple[float, float], sizes: list[float], autofit: dict, measure
) -> TextLayout:
    """Fit text using a supplied ``measure(text, px) -> (width, height)`` callback."""
    max_width, max_height = box
    min_size = float(autofit.get("min_size", min(sizes or [12])))
    step = float(autofit.get("step", 2))
    max_lines = int(autofit.get("max_lines", len(text.splitlines()) or 1))
    original = list(sizes or [36])
    scale = 1.0
    was_reduced = False
    lines = text.splitlines() or [""]
    while True:
        current = [max(min_size, value * scale) for value in original]
        base_size = current[min(len(current) - 1, len(lines) - 1)]
        wrapped = _wrap_words(text, measure, base_size, max_width)
        if len(wrapped) <= max_lines:
            lines = wrapped
        actual_sizes = [current[min(i, len(current) - 1)] for i in range(len(lines))]
        metrics = [measure(line, actual_sizes[i]) for i, line in enumerate(lines)]
        if (
            metrics
            and max((w for w, _ in metrics), default=0) <= max_width
            and sum(h for _, h in metrics) <= max_height
        ):
            warning = f"text reduced to {min(actual_sizes):g} px" if was_reduced else ""
            return TextLayout(
                tuple(lines),
                tuple(actual_sizes),
                tuple(h for _, h in metrics),
                tuple(w for w, _ in metrics),
                warning,
            )
        next_scale = scale - step / max(original)
        if min(value * next_scale for value in original) < min_size:
            final_sizes = [max(min_size, value * scale) for value in original]
            actual = [
                final_sizes[min(i, len(final_sizes) - 1)] for i in range(len(lines))
            ]
            metrics = [measure(line, actual[i]) for i, line in enumerate(lines)]
            return TextLayout(
                tuple(lines),
                tuple(actual),
                tuple(h for _, h in metrics),
                tuple(w for w, _ in metrics),
                f"text does not fit; minimum size {min_size:g} px reached",
            )
        scale = next_scale
        was_reduced = True
