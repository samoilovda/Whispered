"""
Whisper Fedora - Configuration Management
Store and load user settings
"""

import os
import json
from dataclasses import dataclass, field, asdict
from typing import Optional
from pathlib import Path


# Config directory
CONFIG_DIR = Path.home() / ".whisper-fedora"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    """User configuration settings."""
    
    # Hugging Face settings (for pyannote)
    hf_token: Optional[str] = None
    
    # Diarization settings
    diarization_enabled: bool = False
    default_num_speakers: Optional[int] = None  # None = auto-detect
    
    # Batch processing settings
    batch_output_dir: str = ""
    batch_auto_export: bool = True
    
    # LM Studio settings
    lm_studio_url: str = "http://localhost:1234/v1"
    
    # UI preferences
    show_timestamps: bool = True
    show_speaker_labels: bool = True
    
    def save(self) -> bool:
        """Save configuration to file."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(asdict(self), f, indent=2)
            
            return True
        except Exception as e:
            print(f"Failed to save config: {e}")
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
            print(f"Failed to load config: {e}")
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
    print(f"\nCurrent config:")
    for key, value in asdict(config).items():
        # Mask token
        if key == 'hf_token' and value:
            value = value[:8] + "..." if len(value) > 8 else "***"
        print(f"  {key}: {value}")
    
    print(f"\nHF token configured: {config.has_hf_token()}")
