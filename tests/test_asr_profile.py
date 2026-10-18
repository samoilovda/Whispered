"""domain.asr_profile and core.disk_usage (UX-16 / T16)."""

from core.disk_usage import folder_usage
from domain.asr_profile import CUSTOM, PROFILES, PROFILES_BY_KEY, profile_for
from utils import PERFORMANCE_MODES, WHISPER_MODELS


def test_profiles_map_to_real_models_and_modes():
    models = {k for k, _ in WHISPER_MODELS}
    modes = {m[0] for m in PERFORMANCE_MODES}
    for profile in PROFILES:
        assert profile.model in models and profile.performance_mode in modes


def test_profile_for_exact_settings_else_custom():
    balanced = PROFILES_BY_KEY["balanced"]
    assert profile_for(balanced.model, balanced.performance_mode) == "balanced"
    assert profile_for("tiny", "efficiency") == CUSTOM  # exact choice is never hidden


def test_folder_usage(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.bin").write_bytes(b"1234")
    (tmp_path / "y.bin").write_bytes(b"12")
    assert folder_usage(tmp_path) == (6, 2)
    assert folder_usage(tmp_path / "missing") == (0, 0)
