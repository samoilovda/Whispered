"""Localized display labels for transcription option constants."""

from core.i18n import tr
from utils import PERFORMANCE_MODES, WHISPER_LANGUAGES, WHISPER_MODELS


def whisper_model_options() -> list[tuple[str, str]]:
    return [(key, tr(f"whisper_model_{key.replace('-', '_')}")) for key, _ in WHISPER_MODELS]


def whisper_language_options() -> list[tuple[str, str]]:
    return [(key, tr(f"whisper_language_{key}")) for key, _ in WHISPER_LANGUAGES]


def performance_mode_options() -> list[tuple[str, str, float, str]]:
    return [
        (key, tr(f"performance_{key}"), multiplier, tr(f"performance_{key}_description"))
        for key, _label, multiplier, _description in PERFORMANCE_MODES
    ]
