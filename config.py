"""
Whispered - Configuration Management
Store and load user settings
"""

import json
import os
import stat
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Optional
from urllib.parse import urlparse

from core import secrets_store
from core.logger import get_logger
from core.paths import config_dir, config_path

logger = get_logger(__name__)

CONFIG_DIR = config_dir()
CONFIG_FILE = config_path()

# Fields kept out of config.json when an OS keyring is available (see
# core/secrets_store.py). Config.hf_token/yt_openai_api_key/
# yt_anthropic_api_key hold the real value in memory either way — this
# only changes where save()/load() persist it.
_SECRET_FIELDS = ("hf_token", "yt_openai_api_key", "yt_anthropic_api_key")

# Current schema version written to config.json under "schema_version".
# Increment when load() gains a new migration step.
_CURRENT_SCHEMA_VERSION = 1

# Allowed enum values for validated fields.
_VALID_PROVIDERS = frozenset({"lmstudio", "openai", "anthropic"})
_VALID_THEMES = frozenset({"dark", "light"})
_VALID_PERFORMANCE_MODES = frozenset({"fast", "balanced", "accurate"})
_VALID_FPS = frozenset({24, 25, 30, 60})
_VALID_UI_LANGUAGES = frozenset({"auto", "en", "ru"})


@dataclass
class Config:
    """User configuration settings."""

    # Hugging Face settings (for pyannote)
    hf_token: Optional[str] = None

    # Diarization settings
    diarization_enabled: bool = False
    default_num_speakers: Optional[int] = None  # None = auto-detect

    # LM Studio settings
    lm_studio_url: str = "http://localhost:1234/v1"

    # Book pipeline settings
    book_lm_url: str = "http://localhost:1234/v1"
    book_model_name: str = ""             # empty = auto-detect
    book_temperature: float = 0.3         # low temp for precision, not creativity

    # Video pipeline settings
    video_fps: int = 30                   # 24 | 25 | 30 | 60
    video_drop_frame: bool = False        # DF timecode (only meaningful for 30/60 == 29.97/59.94)

    # Transcription defaults (used by settings dialog)
    default_model: str = "large-v3-turbo-q5_0"
    default_language: str = "auto"
    performance_mode: str = "balanced"

    # UI preferences
    theme: str = "dark"
    show_timestamps: bool = True
    show_speaker_labels: bool = True
    sidebar_collapsed: bool = False
    live_diagnostics_expanded: bool = False
    library_collapsed: bool = False

    # UI language
    ui_language: str = "auto"   # "auto" | "en" | "ru"

    # Custom vocabulary / initial prompt for whisper
    custom_vocabulary: list = field(default_factory=list)   # list[str]

    # Recording settings
    mic_device_index: Optional[int] = None   # None = system default

    # Live transcription remains opt-in until the standalone release gate.
    # The batch pipeline never reads this flag.
    live_transcription_enabled: bool = False

    # Course Capture panel: last-used course name, prefixed onto each saved
    # lesson's history title (see ui/course_capture_panel.py). Gated by
    # live_transcription_enabled like the rest of the Live pipeline.
    course_capture_course_name: str = ""

    # AI Chat settings
    chat_context_chars: int = 48_000   # max transcript chars sent as system context

    # Insights / YouTube generation settings
    insights_context_chars: int = 48_000   # max transcript chars sent per insight prompt

    # History / privacy
    history_enabled: bool = True

    # Export formats last selected in the Record view's Export menu
    # (see ui/record_view.py). Persisted so the choice survives restarts.
    export_formats: list = field(default_factory=lambda: ["txt"])

    # Recipes (domain/recipe.py): named step sets. `recipes` holds
    # user-authored Recipe.to_dict() entries; built-ins
    # (domain.recipe.BUILTIN_RECIPES) aren't stored here. `last_recipe` is
    # a Recipe.name — either a built-in's or one from `recipes`. Replaces
    # the pre-redesign launch_preset/chain_steps fields (see
    # docs/UI_REDESIGN_PLAN_2026-09.ru.md, B2/B9) — the loader still
    # migrates an old config's launch_preset/chain_steps JSON keys into
    # last_recipe (see load() below) even though neither is a field here
    # any more.
    recipes: list = field(default_factory=list)
    last_recipe: str = "transcript_only"

    # YouTube AI provider (feature-scoped; local LM Studio stays the default)
    yt_provider: str = "lmstudio"                       # "lmstudio" | "openai" | "anthropic"
    yt_openai_base_url: str = "https://api.openai.com/v1"
    yt_openai_api_key: str = ""
    yt_openai_model: str = "gpt-4o-mini"                 # editable default
    yt_anthropic_api_key: str = ""
    yt_anthropic_model: str = "claude-sonnet-5"          # editable default

    # Cover generator
    cover_template: str = "prosvet_16x9"
    cover_variant: str = "mint"
    cover_layout: str = "duo"
    cover_host_photo: str = ""
    cover_host_name: str = ""
    cover_image_provider: str = "local"
    cover_comfy_url: str = "http://127.0.0.1:8188"
    cover_comfy_workflow: str = ""
    cover_restore_model: str = "gfpgan-1.4"
    cover_upscale_enabled: bool = True
    # The brand-owner has not visually approved the authored 9:16 layout yet.
    cover_export_shorts: bool = False
    cover_jpeg_max_bytes: int = 2_000_000

    def validate(self) -> list[str]:
        """Check field values for common mistakes; return a list of warning
        strings.  Never raises — a config with invalid values is still
        usable; callers decide how to surface the warnings."""
        warnings: list[str] = []

        if self.yt_provider not in _VALID_PROVIDERS:
            warnings.append(
                f"yt_provider={self.yt_provider!r} is not one of"
                f" {sorted(_VALID_PROVIDERS)}"
            )
        if self.theme not in _VALID_THEMES:
            warnings.append(
                f"theme={self.theme!r} is not one of {sorted(_VALID_THEMES)}"
            )
        if self.performance_mode not in _VALID_PERFORMANCE_MODES:
            warnings.append(
                f"performance_mode={self.performance_mode!r} is not one of"
                f" {sorted(_VALID_PERFORMANCE_MODES)}"
            )
        if self.video_fps not in _VALID_FPS:
            warnings.append(
                f"video_fps={self.video_fps} is not one of {sorted(_VALID_FPS)}"
            )
        if self.ui_language not in _VALID_UI_LANGUAGES:
            warnings.append(
                f"ui_language={self.ui_language!r} is not one of"
                f" {sorted(_VALID_UI_LANGUAGES)}"
            )

        for url_field in ("lm_studio_url", "book_lm_url", "yt_openai_base_url"):
            url = getattr(self, url_field, "")
            if url:
                parsed = urlparse(url)
                if parsed.scheme not in ("http", "https"):
                    warnings.append(
                        f"{url_field}={url!r}: expected http or https scheme"
                    )

        return warnings

    def save(self) -> bool:
        """Save configuration to file.

        Secret fields are stored in the OS keyring when one is available
        (see core/secrets_store.py) — config.json gets a sentinel in
        their place instead of the plaintext value. Falls back to storing
        the plaintext value directly, exactly as before, whenever keyring
        isn't available.
        """
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            data = asdict(self)
            # Persist schema version alongside the rest of the config so
            # future load() can run targeted migrations.
            data["schema_version"] = _CURRENT_SCHEMA_VERSION
            for name in _SECRET_FIELDS:
                value = data.get(name)
                if value:
                    if secrets_store.set_secret(name, value):
                        data[name] = secrets_store.KEYRING_SENTINEL
                else:
                    # Clear any stale keyring entry from before the field
                    # was emptied, so it doesn't linger indefinitely.
                    secrets_store.delete_secret(name)

            # Create the temporary file privately before any secret-bearing
            # bytes are written, then atomically replace the old config.
            fd, temporary = tempfile.mkstemp(prefix=".config-", dir=CONFIG_DIR)
            fd_open = True
            try:
                # mkstemp already opens the file 0600; fchmod restates that
                # intent but only exists on POSIX — on Windows the file is
                # created inside the per-user profile and there is no
                # equivalent bit to set.
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    # fdopen owns the descriptor from here on: closing it
                    # again in the handler could hit an unrelated file that
                    # reused the number.
                    fd_open = False
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary, CONFIG_FILE)
            except Exception:
                if fd_open:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise

            return True
        except Exception as e:
            logger.warning("Failed to save config: %s", e)
            return False

    @classmethod
    def load(cls) -> 'Config':
        """Load configuration from file, resolving any secret field that
        was moved into the OS keyring back to its real value."""
        if not CONFIG_FILE.exists():
            return cls()

        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Only use known fields
            known_fields = {f.name for f in cls.__dataclass_fields__.values()}
            filtered_data = {k: v for k, v in data.items() if k in known_fields}

            for name in _SECRET_FIELDS:
                raw = filtered_data.get(name)
                if raw == secrets_store.KEYRING_SENTINEL:
                    result = secrets_store.read_secret(name)
                    if result.found:
                        filtered_data[name] = result.value
                    elif result.backend_error:
                        # Keyring backend is broken — preserve the sentinel so
                        # the next save() does not overwrite the keyring entry
                        # with an empty string.
                        filtered_data[name] = secrets_store.KEYRING_SENTINEL
                        logger.warning(
                            "Keyring backend error reading %s — keeping sentinel",
                            name,
                        )
                    else:
                        # Missing from keyring (entry deleted externally)
                        filtered_data[name] = ""

            # A config saved before recipes existed (docs/UI_REDESIGN_PLAN_2026-09.ru.md,
            # B2) gets last_recipe inferred from whatever legacy step list
            # it has: an explicit chain_steps (the old StepChecklist) if
            # present, else one derived from the older launch_preset.
            # Neither chain_steps nor launch_preset is a Config field any
            # more (B9) — this reads them straight from the raw JSON
            # instead of round-tripping through filtered_data.
            if "last_recipe" not in data:
                raw_steps = data.get("chain_steps")
                if not raw_steps:
                    preset_steps = {
                        "transcribe_only": ["transcript"],
                        "youtube": ["transcript", "youtube"],
                        "article": ["transcript", "article"],
                        "full": ["transcript", "youtube", "article", "insights"],
                    }
                    raw_steps = preset_steps.get(
                        data.get("launch_preset", "transcribe_only"), ["transcript"]
                    )
                steps = set(raw_steps)
                if "youtube" in steps:
                    filtered_data["last_recipe"] = "youtube_video"
                elif "article" in steps:
                    filtered_data["last_recipe"] = "podcast_article"
                elif "book" in steps:
                    filtered_data["last_recipe"] = "book"
                elif "insights" in steps:
                    filtered_data["last_recipe"] = "meeting_notes"
                else:
                    filtered_data["last_recipe"] = "transcript_only"

            instance = cls(**filtered_data)
            warnings = instance.validate()
            for w in warnings:
                logger.warning("Config validation: %s", w)
            return instance
        except Exception as e:
            logger.warning("Failed to load config: %s", e)
            return cls()

    def has_hf_token(self) -> bool:
        """Check if Hugging Face token is configured."""
        return bool(self.hf_token and len(self.hf_token) > 10)


# Global config instance (lazy loaded)
_config: Optional[Config] = None


def get_config() -> Config:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = Config.load()
    return _config


def save_config() -> bool:
    """Save the global configuration."""
    global _config
    if _config is None:
        return False
    return _config.save()


def reset_config() -> Config:
    """Reset configuration to defaults."""
    global _config
    _config = Config()
    _config.save()
    return _config


# CLI for testing
if __name__ == "__main__":
    print(f"Config directory: {CONFIG_DIR}")
    print(f"Config file: {CONFIG_FILE}")

    config = get_config()
    print("\nCurrent config:")
    for key, value in asdict(config).items():
        # Mask any secret-shaped field (token/key in the name), not just
        # hf_token — the yt_openai_api_key / yt_anthropic_api_key fields
        # would otherwise print in plain text.
        if isinstance(value, str) and value and ('token' in key.lower() or 'key' in key.lower()):
            value = value[:8] + "..." if len(value) > 8 else "***"
        print(f"  {key}: {value}")

    print(f"\nHF token configured: {config.has_hf_token()}")
