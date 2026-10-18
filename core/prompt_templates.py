"""The user's prompt templates on disk (N05): copies of built-in prompts.

Each template is one JSON file in ``<data dir>/prompt_templates``; the
built-in ``prompts/*.md`` are never touched. ``Config.prompt_template_active``
maps a built-in prompt name to the template that stands in for it, and
``core.prompts`` consults that. Saving a changed template raises its
version, so everything generated with it is recognised as made from a
different prompt (cache keys follow ``prompt_version``).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from core.logger import get_logger
from domain.ai_profile import ProfileError, PromptTemplate, slug, template_from_dict

logger = get_logger(__name__)


def templates_dir() -> Path:
    from core.paths import data_dir

    path = data_dir() / "prompt_templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def builtin_names() -> list[str]:
    """Built-in prompt names a template may copy."""
    from core.prompts import PROMPTS_DIR

    return sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))


def list_templates() -> list[PromptTemplate]:
    found = []
    for path in sorted(templates_dir().glob("*.json")):
        template = _read(path)
        if template is not None:
            found.append(template)
    return found


def _read(path: Path) -> Optional[PromptTemplate]:
    try:
        return template_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ProfileError) as exc:
        logger.warning("Ignoring unreadable prompt template %s: %s", path.name, exc)
        return None


def load(name: str) -> Optional[PromptTemplate]:
    return _read(templates_dir() / f"{slug(name)}.json")


def make_copy(base: str, name: str) -> PromptTemplate:
    """A new template: a copy of the built-in prompt *base*."""
    from core.prompts import PROMPTS_DIR

    text = (PROMPTS_DIR / f"{base}.md").read_text(encoding="utf-8").strip()
    return PromptTemplate(name=name, base=base, text=text, version=1)


def save(template: PromptTemplate) -> PromptTemplate:
    """Write *template*; a changed text or examples bump its version."""
    previous = load(template.name)
    if previous is not None and (previous.text != template.text
                                 or previous.to_dict()["examples"] != template.to_dict()["examples"]):
        template.version = max(template.version, previous.version) + 1
    path = templates_dir() / f"{slug(template.name)}.json"
    fd, tmp = tempfile.mkstemp(prefix=".tpl.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(template.to_dict(), handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return template


def delete(name: str) -> None:
    try:
        (templates_dir() / f"{slug(name)}.json").unlink()
    except FileNotFoundError:
        pass


def active_for(prompt_name: str) -> Optional[PromptTemplate]:
    """The template standing in for built-in *prompt_name*, or ``None``."""
    from config import get_config

    chosen = (getattr(get_config(), "prompt_template_active", None) or {}).get(prompt_name)
    if not chosen:
        return None
    template = load(chosen)
    return template if template is not None and template.base == prompt_name else None
