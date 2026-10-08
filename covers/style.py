"""Pick a cover's palette and leaf arrangement.

``"auto"`` lets every episode look different without the user choosing:
the pick is seeded by the title, so the same episode always renders the
same cover — the preview, the recipe's ``cover.png`` and the thumbnail
uploaded to YouTube agree — while the "Shuffle" button steps through the
other combinations.
"""

from __future__ import annotations

import hashlib
import random
from typing import Sequence

AUTO = "auto"


def pick_style(
    variants: Sequence[str],
    decor_sets: Sequence[str],
    *,
    variant: str = AUTO,
    decor_set: str = AUTO,
    title: str = "",
    shuffle: int = 0,
) -> tuple[str, str | None]:
    """Resolve ``"auto"`` choices to a concrete ``(variant, decor_set)``.

    An explicit choice is kept as is; ``decor_set`` resolves to ``None``
    when the template has no sets. Every combination the ``"auto"`` slots
    allow is ordered once by a title-seeded shuffle and *shuffle* indexes
    into that order, so consecutive shuffles always differ and cycle
    through every combination before repeating.
    """
    variant_pool = list(variants) if variant == AUTO else [variant]
    if not variant_pool:
        raise ValueError("template has no colour variants")
    decor_pool: list[str | None]
    if decor_set != AUTO:
        decor_pool = [decor_set]
    else:
        decor_pool = list(decor_sets) or [None]
    combos = [(v, d) for v in variant_pool for d in decor_pool]
    key = " ".join(title.split()).casefold()
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    random.Random(int.from_bytes(digest[:8], "big")).shuffle(combos)
    return combos[shuffle % len(combos)]
