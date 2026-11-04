"""Localized display labels for transcription option constants."""

from core.i18n import current_lang, tr
from utils import PERFORMANCE_MODES, WHISPER_LANGUAGES, WHISPER_MODELS


# (translation key, template variant id) for the cover colour combos — the
# Cover workspace and Settings list the same palettes, "auto" first.
COVER_VARIANT_CHOICES: list[tuple[str, str]] = [
    ("cover_variant_auto", "auto"),
    ("cover_variant_sand", "sand"),
    ("cover_variant_mint", "mint"),
    ("cover_variant_latte", "latte"),
    ("cover_variant_honey", "honey"),
    ("cover_variant_lagoon", "lagoon"),
    ("cover_variant_teal", "teal"),
    ("cover_variant_warm", "warm"),
    ("cover_variant_cocoa", "cocoa"),
    ("cover_variant_coral", "coral"),
]


# The models' own names; the same in every language.
_MODEL_NAMES = {
    "tiny": "Tiny", "base": "Base", "small": "Small", "medium": "Medium",
    "large-v3": "Large v3", "large-v3-turbo": "Turbo",
    "large-v3-turbo-q5_0": "Turbo Q5", "large-v3-turbo-q8_0": "Turbo Q8",
}


def size_label(size_bytes: int) -> str:
    """A download size in decimal MB/GB with the UI language's decimal mark
    — the one unit every model label and download note uses (audit
    2026-11-04 W10: one model was "~547 MB" in its label, MiB, and "~574 MB"
    in the same sentence, from the manifest)."""
    if size_bytes >= 1_000_000_000:
        value = f"{size_bytes / 1_000_000_000:.1f}"
        key = "size_gigabytes"
    else:
        value = str(round(size_bytes / 1_000_000))
        key = "size_megabytes"
    if current_lang() == "ru":
        value = value.replace(".", ",")
    return tr(key, value=value)


def whisper_model_name(key: str) -> str:
    """Just the model's name, for use inside a sentence."""
    return _MODEL_NAMES.get(key, key)


def whisper_model_label(key: str) -> str:
    """"Turbo Q5 (~574 MB) — the most compact Turbo": the size comes from
    the download manifest, so it is the size that will actually download."""
    from core.model_manifest import whisper_entry

    entry = whisper_entry(key)
    description = tr(f"whisper_model_desc_{key.replace('-', '_')}")
    if entry is None or not entry.size_bytes:
        return f"{whisper_model_name(key)} — {description}"
    return tr("whisper_model_label", name=whisper_model_name(key),
              size=size_label(entry.size_bytes), description=description)


def whisper_model_options() -> list[tuple[str, str]]:
    return [(key, whisper_model_label(key)) for key, _ in WHISPER_MODELS]


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

    from core.model_manifest import whisper_entry
    from utils import get_models_dir

    models_dir = Path(get_models_dir())
    result = []
    for key, _ in WHISPER_MODELS:
        label = whisper_model_label(key)
        target = models_dir / f"ggml-{key}.bin"
        entry = whisper_entry(key)
        expected_size = entry.size_bytes if entry is not None and entry.size_bytes else None
        downloaded = target.exists() and (
            expected_size is None or target.stat().st_size == expected_size
        )
        result.append((key, label, downloaded))
    return result


def model_state_suffix(downloaded: bool) -> str:
    """Localized "· downloaded" / "· will download" suffix for a model
    combo item — a text signal, not color alone. Each label from
    whisper_model_options() already states the model's size, so the suffix
    doesn't repeat it."""
    return tr("model_state_downloaded" if downloaded else "model_state_not_downloaded")


def whisper_language_options() -> list[tuple[str, str]]:
    return [(key, tr(f"whisper_language_{key}")) for key, _ in WHISPER_LANGUAGES]


def performance_mode_options() -> list[tuple[str, str, float, str]]:
    return [
        (key, tr(f"performance_{key}"), multiplier, tr(f"performance_{key}_description"))
        for key, _label, multiplier, _description in PERFORMANCE_MODES
    ]


def recipe_label(recipe) -> str:
    """A recipe's display label (B4, docs/IMPROVEMENT_PLAN_2026-08.ru.md):
    a built-in's own translated name (``recipe_<builtin_key>``), or a
    custom recipe's own name exactly as the user typed it — there's no
    locale key for an arbitrary user-authored name."""
    return tr(f"recipe_{recipe.builtin_key}") if recipe.builtin_key else recipe.name
