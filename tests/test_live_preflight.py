"""Preflight and 16/24 GB capability-profile tests for L16/L21."""

from pathlib import Path

from core.live.preflight import LivePreflight, PreflightStatus, resource_profile


def test_resource_profiles_degrade_partial_frequency_without_disabling_16gb():
    minimum = resource_profile(16)
    recommended = resource_profile(24)
    unsupported = resource_profile(8)

    assert minimum.supported is True
    assert minimum.partial_interval_seconds > recommended.partial_interval_seconds
    assert recommended.name == "recommended"
    assert unsupported.supported is False


def test_preflight_blocks_missing_source_and_missing_system_helper(tmp_path, monkeypatch):
    monkeypatch.setattr("core.live.preflight.platform.system", lambda: "Darwin")
    preflight = LivePreflight()
    no_source = preflight.run(
        use_mic=False, use_system=False, model_name="tiny", memory_gb=24
    )
    assert preflight.can_start(no_source) is False

    missing_helper = preflight.run(
        use_mic=False,
        use_system=True,
        model_name="tiny",
        helper_path=Path(tmp_path / "missing"),
        memory_gb=24,
    )
    system = next(check for check in missing_helper if check.key == "system_audio")
    assert system.status is PreflightStatus.FAIL


def test_preflight_rejects_windows_system_audio(monkeypatch):
    monkeypatch.setattr("core.live.preflight.platform.system", lambda: "Windows")
    checks = LivePreflight().run(
        use_mic=True, use_system=True, model_name="tiny", memory_gb=24
    )
    system = next(check for check in checks if check.key == "system_audio")
    assert system.status is PreflightStatus.FAIL
    assert "Windows" in system.message


def test_model_check_wants_the_exact_model_file(tmp_path, monkeypatch):
    """Only ggml-large-v3-turbo-q8_0.bin on disk: the full turbo model is
    not ready (a glob on the name used to say it was)."""
    monkeypatch.setattr("utils.get_models_dir", lambda: str(tmp_path))
    (tmp_path / "ggml-large-v3-turbo-q8_0.bin").write_bytes(b"\0")

    def model_check(name):
        checks = LivePreflight().run(
            use_mic=True, use_system=False, model_name=name, memory_gb=24)
        return next(check for check in checks if check.key == "model").status

    assert model_check("large-v3-turbo") is PreflightStatus.WARNING
    assert model_check("large-v3-turbo-q8_0") is PreflightStatus.PASS
