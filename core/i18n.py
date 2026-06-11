import json
import os
from pathlib import Path
from core.logger import get_logger

logger = get_logger(__name__)

_current_lang = "en"
_translations = {}

def set_language(lang: str):
    """Set the UI language and load translations."""
    global _current_lang, _translations
    _current_lang = lang
    _translations.clear()
    
    if lang == "en":
        return  # English is the default in source
        
    locale_file = Path(__file__).parent.parent / "locales" / f"{lang}.json"
    if locale_file.exists():
        try:
            with open(locale_file, 'r', encoding='utf-8') as f:
                _translations = json.load(f)
            logger.info(f"Loaded translations for {lang}")
        except Exception as e:
            logger.error(f"Failed to load translations for {lang}: {e}")
    else:
        logger.warning(f"Translation file not found: {locale_file}")

def tr(text: str) -> str:
    """Translate a string based on the current language."""
    if _current_lang == "en":
        return text
    return _translations.get(text, text)
