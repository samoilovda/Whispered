"""
Whispered - Prompt Loader
Loads system prompts from the prompts/ directory as editable .md files.
"""

import hashlib
from pathlib import Path
from core.logger import get_logger

logger = get_logger(__name__)

# Prompts directory: <project_root>/prompts/
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _active_template(name: str):
    """The user's template standing in for built-in *name* (N05), if one is
    chosen — never raises: a broken template means the built-in is used."""
    try:
        from core.prompt_templates import active_for

        return active_for(name)
    except Exception as exc:  # noqa: BLE001 - provenance and prompts must not fail on this
        logger.warning("Prompt template lookup failed for %s: %s", name, exc)
        return None


def load_prompt(name: str, fallback: str = "") -> str:
    """
    Load a prompt from prompts/<name>.md.

    Args:
        name: Prompt file name without extension (e.g. 'cleaning', 'расшивка').
        fallback: Default text to use if file is not found.

    Returns:
        The prompt text (stripped), or fallback string if file missing.
    """
    override = _active_template(name)
    if override is not None:
        return override.rendered().strip()
    prompt_path = PROMPTS_DIR / f"{name}.md"
    try:
        text = prompt_path.read_text(encoding="utf-8").strip()
        if not text:
            logger.warning("Prompt file is empty: %s", prompt_path)
            return fallback
        return text
    except FileNotFoundError:
        logger.warning(
            "Prompt file not found: %s — using built-in fallback", prompt_path
        )
        return fallback
    except Exception as e:
        logger.warning("Failed to load prompt %s: %s — using fallback", name, e)
        return fallback


def prompt_version(name: str) -> str:
    """A stable identifier for a prompt file's current content.

    Used by Artifact.cache_key() (domain/artifact.py) to decide whether a
    generated file can be reused: two artifacts with the same
    prompt_version were produced from the exact same prompt text, so
    editing prompts/<name>.md (the whole point of it being an editable
    .md file — see docs/agents/llm.md) invalidates the cache for that step, same
    as changing the model or provider does. Returns a fixed placeholder
    for a missing/unreadable file rather than raising — provenance must
    never block a generation that would otherwise succeed.
    """
    override = _active_template(name)
    if override is not None:
        stamp = f"template:{override.name}:v{override.version}:{override.rendered()}"
        return hashlib.sha256(stamp.encode("utf-8")).hexdigest()[:16]
    prompt_path = PROMPTS_DIR / f"{name}.md"
    try:
        data = prompt_path.read_bytes()
    except OSError:
        return "no-prompt-file"
    return hashlib.sha256(data).hexdigest()[:16]



