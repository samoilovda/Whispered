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


# Short function words that read wrong at the end of a title line
# ("Почему психологу трудно в / личной терапии?"): each is kept on the
# same line as the word after it. Any one- or two-letter word counts too.
_LEADING_WORDS = frozenset({
    "без", "для", "изо", "как", "над", "обо", "или", "под", "при", "про",
    "что", "чем", "это",
    "and", "for", "the", "but", "nor",
})
_DASHES = frozenset({"-", "–", "—"})
# How much wider than the balanced optimum a line may grow so the first
# line can take the extra words (see ``_balance``).
_BALANCE_SLACK = 1.15


def _tokens(words: list[str]) -> list[str]:
    """Glue words that must not be separated by a line break: a short
    function word to the word after it, a dash to the word before it."""
    tokens: list[str] = []
    carry = ""
    for word in words:
        if word in _DASHES and tokens and not carry:
            tokens[-1] = f"{tokens[-1]} {word}"
            continue
        joined = f"{carry} {word}" if carry else word
        bare = word.strip("«»\"'()[].,:;!?").lower()
        if len(bare) <= 2 or bare in _LEADING_WORDS:
            carry = joined
            continue
        tokens.append(joined)
        carry = ""
    if carry:
        if tokens:
            tokens[-1] = f"{tokens[-1]} {carry}"
        else:
            tokens.append(carry)
    return tokens


def _greedy(tokens: list[str], fits) -> list[str]:
    """Fill each line with as many tokens as ``fits(line)`` allows."""
    lines, line = [], tokens[0]
    for token in tokens[1:]:
        proposed = f"{line} {token}"
        if fits(proposed):
            line = proposed
        else:
            lines.append(line)
            line = token
    lines.append(line)
    return lines


def _balance(
    tokens: list[str], count: int, measure, size: float, max_width: float
) -> list[str] | None:
    """Split *tokens* into exactly *count* lines that all fit, minimising
    the widest line — so a two-line title reads as two similar halves
    rather than a full line and a stub. ``None`` when no split fits."""
    widths: dict[tuple[int, int], float] = {}

    def width(start: int, end: int) -> float:
        key = (start, end)
        if key not in widths:
            widths[key] = measure(" ".join(tokens[start:end]), size)[0]
        return widths[key]

    best: dict[tuple[int, int], tuple[float, list[int]] | None] = {}

    def solve(start: int, lines: int) -> tuple[float, list[int]] | None:
        key = (start, lines)
        if key in best:
            return best[key]
        result: tuple[float, list[int]] | None = None
        if lines == 1:
            w = width(start, len(tokens))
            result = (w, [len(tokens)]) if w <= max_width else None
        else:
            for end in range(start + 1, len(tokens) - lines + 2):
                head = width(start, end)
                if head > max_width:
                    break
                rest = solve(end, lines - 1)
                if rest is None:
                    continue
                candidate = (max(head, rest[0]), [end, *rest[1]])
                if result is None or candidate[0] < result[0]:
                    result = candidate
        best[key] = result
        return result

    solution = solve(0, count)
    if solution is None:
        return None
    # The strict optimum can flip a title upside down ("Почему психологу /
    # трудно в личной терапии?"). Re-wrap greedily within a little slack
    # over the optimum instead: still balanced, but the first line gets the
    # extra words, as a designer would set it. Greedy wrapping at any width
    # >= the optimum never needs more than *count* lines.
    limit = min(max_width, solution[0] * _BALANCE_SLACK)
    return _greedy(tokens, lambda line: measure(line, size)[0] <= limit)


def _wrap_words(text: str, measure, size: float, max_width: float) -> list[str]:
    result: list[str] = []
    for paragraph in text.splitlines() or [""]:
        tokens = _tokens(paragraph.split())
        if not tokens:
            result.append("")
            continue
        greedy = _greedy(tokens, lambda line: measure(line, size)[0] <= max_width)
        if len(greedy) > 1:
            balanced = _balance(tokens, len(greedy), measure, size, max_width)
            if balanced is not None:
                greedy = balanced
        result.extend(greedy)
    return result


# A layout with more lines must be at least this much larger to win over
# one with fewer: a title that nearly fits on one line stays on one.
_MORE_LINES_GAIN = 1.03
# How much size a multi-line title may give up so its first line is the
# longer one ("Почему психологу трудно / в личной терапии?") rather than a
# short head over a long tail.
_TOP_HEAVY_GIVE = 0.92
# Size search stops once the bracket is narrower than this (px).
_SIZE_PRECISION = 0.5


def _layout(
    paragraphs: list[list[str]], lines_wanted: int | None, base: float,
    ratios: list[float], measure, box: tuple[float, float],
) -> TextLayout | None:
    """Lay *paragraphs* out at *base* px, or ``None`` if they overflow *box*.
    *lines_wanted* splits a single paragraph into exactly that many
    balanced lines; ``None`` wraps every paragraph to the box width."""
    max_width, max_height = box
    lines: list[str] = []
    if lines_wanted is not None:
        tokens = paragraphs[0]
        if lines_wanted == 1:
            lines = [" ".join(tokens)]
        else:
            balanced = _balance(tokens, lines_wanted, measure, base, max_width)
            if balanced is None:
                return None
            lines = balanced
    else:
        for tokens in paragraphs:
            if not tokens:
                lines.append("")
                continue
            lines.extend(
                _greedy(tokens, lambda line: measure(line, base)[0] <= max_width)
            )
    sizes = [base * ratios[min(i, len(ratios) - 1)] for i in range(len(lines))]
    metrics = [measure(line, px) for line, px in zip(lines, sizes)]
    if (
        max(w for w, _ in metrics) > max_width
        or sum(h for _, h in metrics) > max_height
    ):
        return None
    return TextLayout(
        tuple(lines), tuple(sizes),
        tuple(h for _, h in metrics), tuple(w for w, _ in metrics),
    )


def _largest(build, low: float, high: float) -> TextLayout | None:
    """The layout at the largest size in [low, high] that ``build`` accepts
    (overflow only grows with size, so a bisection finds it)."""
    best = build(low)
    if best is None:
        return None
    top = build(high)
    if top is not None:
        return top
    while high - low > _SIZE_PRECISION:
        middle = (low + high) / 2
        attempt = build(middle)
        if attempt is None:
            high = middle
        else:
            low, best = middle, attempt
    return best


def fit_text(
    text: str, box: tuple[float, float], sizes: list[float], autofit: dict, measure
) -> TextLayout:
    """Fit text using a supplied ``measure(text, px) -> (width, height)`` callback.

    The first entry of *sizes* is the design size; further entries set the
    following lines' size relative to it. ``autofit`` keys: ``min_size``
    (never smaller), ``max_size`` (short text may grow up to it — defaults
    to the design size, i.e. shrink-only), ``max_lines``. Every line count
    from one to ``max_lines`` is tried at its largest fitting size, and the
    biggest wins, so a short title fills the box on one line while a long
    one breaks into balanced lines rather than shrinking on one. Text with
    explicit line breaks keeps them and is only scaled.
    """
    original = list(sizes or [36])
    base = original[0]
    ratios = [value / base for value in original]
    min_size = float(autofit.get("min_size", min(original)))
    max_size = max(float(autofit.get("max_size", base)), min_size)
    paragraphs = [_tokens(line.split()) for line in (text.splitlines() or [""])]
    max_lines = int(autofit.get("max_lines", len(paragraphs) or 1))

    candidates: list[TextLayout] = []
    if len(paragraphs) == 1 and paragraphs[0]:
        for count in range(1, min(max_lines, len(paragraphs[0])) + 1):
            found = _largest(
                lambda px, n=count: _layout(paragraphs, n, px, ratios, measure, box),
                min_size, max_size,
            )
            if found is not None:
                candidates.append(found)
    else:
        cap = max(max_lines, len(paragraphs))

        def capped(px: float) -> TextLayout | None:
            layout = _layout(paragraphs, None, px, ratios, measure, box)
            return layout if layout is not None and len(layout.lines) <= cap else None

        found = _largest(capped, min_size, max_size)
        if found is not None:
            candidates.append(found)
    if candidates:
        best = candidates[0]
        for candidate in candidates[1:]:
            if candidate.sizes[0] > best.sizes[0] * _MORE_LINES_GAIN:
                best = candidate
        if len(paragraphs) == 1 and len(best.lines) > 1 and best.widths[0] < best.widths[-1]:
            count, px = len(best.lines), best.sizes[0]
            while px - 1 >= max(min_size, best.sizes[0] * _TOP_HEAVY_GIVE):
                px -= 1
                retry = _layout(paragraphs, count, px, ratios, measure, box)
                if retry is not None and retry.widths[0] >= retry.widths[-1]:
                    return retry
        return best

    # Nothing fits even at the minimum size: wrap at the minimum and say so.
    lines = _wrap_words(text, measure, min_size, box[0])
    actual = [min_size * ratios[min(i, len(ratios) - 1)] for i in range(len(lines))]
    metrics = [measure(line, px) for line, px in zip(lines, actual)]
    return TextLayout(
        tuple(lines),
        tuple(actual),
        tuple(h for _, h in metrics),
        tuple(w for w, _ in metrics),
        f"text does not fit; minimum size {min_size:g} px reached",
    )
