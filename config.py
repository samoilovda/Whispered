"""
Whispered - Configuration Management
Store and load user settings
"""

import json
import os
import stat
from dataclasses import dataclass, field, asdict
from typing import Optional

from core.logger import get_logger
from core.paths import data_dir

logger = get_logger(__name__)

CONFIG_DIR = data_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"


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

    # UI language
    ui_language: str = "auto"   # "auto" | "en" | "ru"

    # Custom vocabulary / initial prompt for whisper
    custom_vocabulary: list = field(default_factory=list)   # list[str]

    # Recording settings
    mic_device_index: Optional[int] = None   # None = system default

    # Live transcription remains opt-in until the standalone release gate.
    # The batch pipeline never reads this flag.
    live_transcription_enabled: bool = False

    # AI Chat settings
    chat_context_chars: int = 48_000   # max transcript chars sent as system context

    # Insights / YouTube generation settings
    insights_context_chars: int = 48_000   # max transcript chars sent per insight prompt

    # History / privacy
    history_enabled: bool = True

    # Export formats last selected in the Record view's Export menu
    # (see ui/record_view.py). Persisted so the choice survives restarts.
    export_formats: list = field(default_factory=lambda: ["txt"])

    # Last-selected preset in the Library's launch bar (see ui/launch_bar.py).
    launch_preset: str = "transcribe_only"

    # YouTube AI provider (feature-scoped; local LM Studio stays the default)
    yt_provider: str = "lmstudio"                       # "lmstudio" | "openai" | "anthropic"
    yt_openai_base_url: str = "https://api.openai.com/v1"
    yt_openai_api_key: str = ""
    yt_openai_model: str = "gpt-4o-mini"                 # editable default
    yt_anthropic_api_key: str = ""
    yt_anthropic_model: str = "claude-sonnet-5"          # editable default

    def save(self) -> bool:
        """Save configuration to file."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)

            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(asdict(self), f, indent=2)

            # Config may contain secrets (e.g. hf_token) — restrict to owner read/write.
            try:
                os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)
            except OSError as e:
                logger.warning("Failed to restrict config file permissions: %s", e)

            return True
        except Exception as e:
            logger.warning("Failed to save config: %s", e)
            return False

    @classmethod
    def load(cls) -> 'Config':
        """Load configuration from file."""
        if not CONFIG_FILE.exists():
            return cls()

        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Only use known fields
            known_fields = {f.name for f in cls.__dataclass_fields__.values()}
            filtered_data = {k: v for k, v in data.items() if k in known_fields}

            return cls(**filtered_data)
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
        if value and ('token' in key.lower() or 'key' in key.lower()):
            value = value[:8] + "..." if len(value) > 8 else "***"
        print(f"  {key}: {value}")

    print(f"\nHF token configured: {config.has_hf_token()}")
