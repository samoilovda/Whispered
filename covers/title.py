"""Parsing and validation for LLM-proposed cover titles."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TitleSuggestion:
    text: str
    warnings: tuple[str, ...] = ()


def parse_title_suggestions(response: str) -> list[TitleSuggestion]:
    cleaned = response.replace("```", "").strip()
    blocks = re.split(r"\n\s*\n|\n(?=\s*(?:ВАРИАНТ\s*)?\d+[).:-])", cleaned, flags=re.I)
    result: list[TitleSuggestion] = []
    for block in blocks:
        lines = [
            re.sub(r"^\s*(?:ВАРИАНТ\s*)?\d+[).:-]?\s*", "", line, flags=re.I).strip(
                " \"'.—-"
            )
            for line in block.splitlines()
            if line.strip()
        ]
        lines = [
            line
            for line in lines
            if line and not re.match(r"^(вот|варианты|предлагаю)", line, re.I)
        ]
        if not lines:
            continue
        # Some models prefix two lines with 1.1 / 1.2; keep one proposal per pair.
        for index in range(0, len(lines), 2):
            pair = lines[index : index + 2]
            if len(pair) < 2:
                continue
            warnings = []
            if len(pair[0]) > 22:
                warnings.append("первая строка длиннее 22 символов")
            if len(pair[1]) > 34:
                warnings.append("вторая строка длиннее 34 символов")
            result.append(
                TitleSuggestion(
                    "\n".join(line.upper() for line in pair), tuple(warnings)
                )
            )
    return result[:3]
