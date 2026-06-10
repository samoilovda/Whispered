"""
Whispered - Prompt Loader
Loads system prompts from the prompts/ directory as editable .md files.
"""

from pathlib import Path
from core.logger import get_logger

logger = get_logger(__name__)

# Prompts directory: <project_root>/prompts/
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(name: str, fallback: str = "") -> str:
    """
    Load a prompt from prompts/<name>.md.

    Args:
        name: Prompt file name without extension (e.g. 'cleaning', 'расшивка').
        fallback: Default text to use if file is not found.

    Returns:
        The prompt text (stripped), or fallback string if file missing.
    """
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


def list_prompts() -> list[str]:
    """Return names (without .md extension) of all available prompt files."""
    if not PROMPTS_DIR.exists():
        return []
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.md"))
