"""Localized display labels for transcription option constants."""

from core.i18n import tr
from utils import PERFORMANCE_MODES, WHISPER_LANGUAGES, WHISPER_MODELS


def whisper_model_options() -> list[tuple[str, str]]:
    return [(key, tr(f"whisper_model_{key.replace('-', '_')}")) for key, _ in WHISPER_MODELS]


def whisper_model_options_with_state() -> list[tuple[str, str, bool]]:
    """(key, label, downloaded) for each whisper model (B10,
    docs/IMPROVEMENT_PLAN_2026-08.ru.md).

    "downloaded" is a cheap existence + expected-size check against the
    same ``ggml-{key}.bin`` path transcriber.py's own
    ``Transcriber.prepare_models()`` looks for — not
    ``core.model_repository.ModelRepository.validate()``, which computes
    a sha256 once a manifest entry has one filled in (all currently
    don't, but the plan is explicit this must never become a per-item
    cost paid on every combo build). Called once when the combo is
    (re)built, never from paintEvent or on every dropdown open.
    """
    from pathlib import Path

    from core.model_manifest import MANIFEST
    from utils import get_models_dir

    models_dir = Path(get_models_dir())
    result = []
    for key, _ in WHISPER_MODELS:
        label = tr(f"whisper_model_{key.replace('-', '_')}")
        target = models_dir / f"ggml-{key}.bin"
        entry = MANIFEST.get(f"whisper-{key}")
        expected_size = entry.size_bytes if entry is not None and entry.size_bytes else None
        downloaded = target.exists() and (
            expected_size is None or target.stat().st_size == expected_size
        )
        result.append((key, label, downloaded))
    return result


def model_state_suffix(downloaded: bool) -> str:
    """Localized "· downloaded" / "· will download" suffix for a model
    combo item — a text signal, not color alone (project convention, see
    CLAUDE.md). Each label from whisper_model_options() already states
    the model's size, so the suffix doesn't repeat it."""
    return tr("model_state_downloaded" if downloaded else "model_state_not_downloaded")


def whisper_language_options() -> list[tuple[str, str]]:
    return [(key, tr(f"whisper_language_{key}")) for key, _ in WHISPER_LANGUAGES]


def performance_mode_options() -> list[tuple[str, str, float, str]]:
    return [
        (key, tr(f"performance_{key}"), multiplier, tr(f"performance_{key}_description"))
        for key, _label, multiplier, _description in PERFORMANCE_MODES
    ]
