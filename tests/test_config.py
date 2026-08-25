"""Tests for config.py — no Qt, no network required."""
import json
import stat
import sys
import pytest

# Qt and core.lm_client/core.ai_worker stand-ins come from tests/conftest.py.
import config as config_module
from config import Config


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config storage to a temp directory for every test, and
    force secrets_store to behave as if no OS keyring is available — so
    the ordinary test suite never touches the developer's real Keychain/
    Credential Manager (it is genuinely reachable from this process: see
    TestConfigKeyringIntegration below, which opts back in deliberately
    via an in-memory fake instead)."""
    monkeypatch.setattr(config_module, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config_module, "_config", None)
    monkeypatch.setattr(config_module.secrets_store, "set_secret", lambda name, value: False)
    monkeypatch.setattr(config_module.secrets_store, "get_secret", lambda name: None)
    monkeypatch.setattr(config_module.secrets_store, "delete_secret", lambda name: None)
    yield
    monkeypatch.setattr(config_module, "_config", None)


class _FakeKeyringBackend:
    """In-memory stand-in for the OS keyring, used only by tests that
    deliberately exercise the keyring-integration path."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def set_secret(self, name: str, value: str) -> bool:
        self.store[name] = value
        return True

    def get_secret(self, name: str):
        return self.store.get(name)

    def read_secret(self, name: str):
        from core.secrets_store import SecretReadResult
        value = self.store.get(name)
        if value is None:
            return SecretReadResult(missing=True)
        return SecretReadResult(found=True, value=value)

    def delete_secret(self, name: str) -> None:
        self.store.pop(name, None)


@pytest.fixture
def fake_keyring(monkeypatch):
    backend = _FakeKeyringBackend()
    monkeypatch.setattr(config_module.secrets_store, "set_secret", backend.set_secret)
    monkeypatch.setattr(config_module.secrets_store, "get_secret", backend.get_secret)
    monkeypatch.setattr(config_module.secrets_store, "read_secret", backend.read_secret)
    monkeypatch.setattr(config_module.secrets_store, "delete_secret", backend.delete_secret)
    return backend


class TestConfigDefaults:
    def test_default_lm_url(self):
        cfg = Config()
        assert cfg.lm_studio_url == "http://localhost:1234/v1"

    def test_default_theme(self):
        cfg = Config()
        assert cfg.theme == "dark"

    def test_default_diarization_disabled(self):
        cfg = Config()
        assert not cfg.diarization_enabled

    def test_has_hf_token_false_when_none(self):
        cfg = Config()
        assert not cfg.has_hf_token()

    def test_has_hf_token_false_when_short(self):
        cfg = Config(hf_token="abc")
        assert not cfg.has_hf_token()

    def test_has_hf_token_true(self):
        cfg = Config(hf_token="hf_" + "x" * 40)
        assert cfg.has_hf_token()

    def test_default_yt_provider_is_lmstudio(self):
        cfg = Config()
        assert cfg.yt_provider == "lmstudio"
        assert cfg.yt_openai_api_key == ""
        assert cfg.yt_anthropic_api_key == ""

    def test_live_transcription_is_disabled_by_default(self):
        assert Config().live_transcription_enabled is False


class TestConfigRoundTrip:
    def test_save_and_load(self, tmp_path):
        cfg = Config(
            lm_studio_url="http://192.168.1.1:5000/v1",
            theme="light",
            diarization_enabled=True,
            default_num_speakers=3,
            book_temperature=0.7,
            yt_provider="anthropic",
            yt_anthropic_api_key="ak-test",
            yt_anthropic_model="claude-sonnet-5",
        )
        cfg.save()
        loaded = Config.load()
        assert loaded.lm_studio_url == "http://192.168.1.1:5000/v1"
        assert loaded.theme == "light"
        assert loaded.diarization_enabled is True
        assert loaded.default_num_speakers == 3
        assert abs(loaded.book_temperature - 0.7) < 1e-9
        assert loaded.yt_provider == "anthropic"
        assert loaded.yt_anthropic_api_key == "ak-test"
        assert loaded.yt_anthropic_model == "claude-sonnet-5"

    def test_old_config_without_yt_fields_loads_on_defaults(self, tmp_path):
        cfg_file = config_module.CONFIG_FILE
        data = {"theme": "light", "lm_studio_url": "http://x/v1"}
        cfg_file.write_text(json.dumps(data), encoding="utf-8")
        loaded = Config.load()
        assert loaded.yt_provider == "lmstudio"
        assert loaded.yt_openai_base_url == "https://api.openai.com/v1"
        assert loaded.live_transcription_enabled is False

    @pytest.mark.parametrize(
        ("preset", "steps"),
        [
            ("transcribe_only", ["transcript"]),
            ("youtube", ["transcript", "youtube"]),
            ("article", ["transcript", "article"]),
            ("full", ["transcript", "youtube", "article", "insights"]),
        ],
    )
    def test_legacy_launch_preset_migrates_to_chain_steps(self, preset, steps):
        config_module.CONFIG_FILE.write_text(
            json.dumps({"launch_preset": preset}), encoding="utf-8"
        )

        loaded = Config.load()

        assert loaded.chain_steps == steps

    def test_explicit_chain_steps_are_not_overwritten_by_legacy_preset(self):
        config_module.CONFIG_FILE.write_text(
            json.dumps(
                {
                    "launch_preset": "full",
                    "chain_steps": ["transcript", "book"],
                }
            ),
            encoding="utf-8",
        )

        loaded = Config.load()

        assert loaded.chain_steps == ["transcript", "book"]

    @pytest.mark.parametrize(
        ("chain_steps", "expected_recipe"),
        [
            (["transcript"], "transcript_only"),
            (["transcript", "youtube"], "youtube_video"),
            (["transcript", "article"], "podcast_article"),
            (["transcript", "book"], "book"),
            (["transcript", "insights"], "meeting_notes"),
            (["transcript", "youtube", "article", "insights"], "youtube_video"),
        ],
    )
    def test_legacy_chain_steps_migrate_to_last_recipe(self, chain_steps, expected_recipe):
        """See docs/UI_REDESIGN_PLAN_2026-09.ru.md, B2 — same seeding
        pattern as launch_preset -> chain_steps, one layer further."""
        config_module.CONFIG_FILE.write_text(
            json.dumps({"chain_steps": chain_steps}), encoding="utf-8"
        )

        loaded = Config.load()

        assert loaded.last_recipe == expected_recipe

    def test_legacy_launch_preset_migrates_all_the_way_to_last_recipe(self):
        """launch_preset -> chain_steps -> last_recipe, chained through
        both migrations from a config saved before either existed."""
        config_module.CONFIG_FILE.write_text(
            json.dumps({"launch_preset": "youtube"}), encoding="utf-8"
        )

        loaded = Config.load()

        assert loaded.chain_steps == ["transcript", "youtube"]
        assert loaded.last_recipe == "youtube_video"

    def test_explicit_last_recipe_is_not_overwritten(self):
        config_module.CONFIG_FILE.write_text(
            json.dumps(
                {"chain_steps": ["transcript", "youtube"], "last_recipe": "book"}
            ),
            encoding="utf-8",
        )

        loaded = Config.load()

        assert loaded.last_recipe == "book"

    def test_recipes_field_defaults_empty_and_round_trips(self):
        assert Config().recipes == []
        config_module.CONFIG_FILE.write_text(
            json.dumps({"recipes": [{"name": "my recipe", "steps": ["transcribe"]}]}),
            encoding="utf-8",
        )

        loaded = Config.load()

        assert loaded.recipes == [{"name": "my recipe", "steps": ["transcribe"]}]

    def test_load_missing_file_returns_defaults(self):
        loaded = Config.load()
        assert loaded.theme == "dark"

    def test_unknown_fields_ignored(self, tmp_path):
        cfg_file = config_module.CONFIG_FILE
        data = {"theme": "light", "future_field_xyz": "value", "lm_studio_url": "http://x/v1"}
        cfg_file.write_text(json.dumps(data), encoding="utf-8")
        loaded = Config.load()
        assert loaded.theme == "light"
        assert loaded.lm_studio_url == "http://x/v1"
        # future_field_xyz should simply be ignored
        assert not hasattr(loaded, "future_field_xyz")

    def test_corrupted_json_returns_defaults(self, tmp_path):
        config_module.CONFIG_FILE.write_text("{not valid json", encoding="utf-8")
        loaded = Config.load()
        assert loaded.theme == "dark"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file permissions only")
    def test_save_restricts_file_permissions(self):
        cfg = Config(hf_token="hf_" + "x" * 40)
        cfg.save()
        mode = stat.S_IMODE(config_module.CONFIG_FILE.stat().st_mode)
        assert mode == stat.S_IRUSR | stat.S_IWUSR


class TestGlobalHelpers:
    def test_get_config_singleton(self):
        from config import get_config
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_save_config_writes_file(self):
        from config import get_config, save_config
        cfg = get_config()
        cfg.theme = "light"
        result = save_config()
        assert result is True
        assert config_module.CONFIG_FILE.exists()
        data = json.loads(config_module.CONFIG_FILE.read_text())
        assert data["theme"] == "light"

    def test_reset_config(self):
        from config import get_config, reset_config
        cfg = get_config()
        cfg.theme = "light"
        reset = reset_config()
        assert reset.theme == "dark"


class TestConfigKeyringIntegration:
    """Config.save()/load() against a fake in-memory keyring backend —
    never the real OS one (see the fake_keyring fixture above)."""

    def test_secret_stored_via_keyring_is_not_written_to_json_plaintext(self, fake_keyring):
        cfg = Config(hf_token="hf_" + "x" * 40)
        cfg.save()

        on_disk = json.loads(config_module.CONFIG_FILE.read_text())
        assert on_disk["hf_token"] == config_module.secrets_store.KEYRING_SENTINEL
        assert fake_keyring.store["hf_token"] == "hf_" + "x" * 40

    def test_secret_resolves_from_keyring_on_load(self, fake_keyring):
        cfg = Config(yt_anthropic_api_key="ak-test")
        cfg.save()

        loaded = Config.load()

        assert loaded.yt_anthropic_api_key == "ak-test"

    def test_load_survives_a_keyring_that_lost_the_entry(self, fake_keyring):
        """The sentinel is on disk but the keyring lookup comes back
        empty (e.g. keyring package uninstalled since the last save) —
        must not crash, and the field reads as not-configured."""
        cfg = Config(yt_openai_api_key="sk-test")
        cfg.save()
        fake_keyring.store.clear()

        loaded = Config.load()

        assert loaded.yt_openai_api_key == ""

    def test_clearing_a_field_deletes_its_keyring_entry(self, fake_keyring):
        cfg = Config(hf_token="hf_" + "x" * 40)
        cfg.save()
        assert "hf_token" in fake_keyring.store

        cfg.hf_token = None
        cfg.save()

        assert "hf_token" not in fake_keyring.store
        on_disk = json.loads(config_module.CONFIG_FILE.read_text())
        assert on_disk["hf_token"] is None

    def test_pre_existing_plaintext_secret_still_loads_once_keyring_becomes_available(
        self, fake_keyring
    ):
        """An install that saved its token before this feature existed
        has the real value sitting in config.json, not the sentinel.
        The first load after upgrading must not lose it — migration into
        the keyring only happens passively, on the next save()."""
        config_module.CONFIG_FILE.write_text(
            json.dumps({"hf_token": "hf_" + "y" * 40}), encoding="utf-8"
        )

        loaded = Config.load()

        assert loaded.hf_token == "hf_" + "y" * 40
        assert fake_keyring.store == {}   # nothing migrated yet

        loaded.save()

        on_disk = json.loads(config_module.CONFIG_FILE.read_text())
        assert on_disk["hf_token"] == config_module.secrets_store.KEYRING_SENTINEL
        assert fake_keyring.store["hf_token"] == "hf_" + "y" * 40

    def test_non_secret_fields_unaffected_by_keyring(self, fake_keyring):
        cfg = Config(theme="light", hf_token="hf_" + "x" * 40)
        cfg.save()
        loaded = Config.load()
        assert loaded.theme == "light"

class TestConfigValidation:
    """R10: Config.validate() logs warnings but keeps the config loadable."""

    def test_invalid_yt_provider_loads_but_warns(self, caplog):
        import logging
        import config as config_module

        config_module.CONFIG_FILE.write_text(
            '{"yt_provider": "typo"}', encoding="utf-8"
        )
        with caplog.at_level(logging.WARNING):
            loaded = Config.load()

        # Config loads successfully — invalid value preserved
        assert loaded.yt_provider == "typo"
        # But a warning was logged
        assert any("yt_provider" in r.message for r in caplog.records)

    def test_invalid_theme_is_flagged(self):
        cfg = Config(theme="banana")
        warnings = cfg.validate()
        assert any("theme" in w for w in warnings)

    def test_valid_config_has_no_warnings(self):
        cfg = Config()  # all defaults are valid
        assert cfg.validate() == []

    def test_bad_url_scheme_is_flagged(self):
        cfg = Config(lm_studio_url="ftp://localhost:1234/v1")
        warnings = cfg.validate()
        assert any("lm_studio_url" in w for w in warnings)

    def test_invalid_fps_is_flagged(self):
        cfg = Config(video_fps=15)
        warnings = cfg.validate()
        assert any("video_fps" in w for w in warnings)

    def test_schema_version_written_to_json(self, tmp_path):
        import json
        cfg = Config()
        cfg.save()
        data = json.loads(config_module.CONFIG_FILE.read_text())
        assert "schema_version" in data
        assert data["schema_version"] >= 1


class TestConfigBackendError:
    """R10: backend_error during keyring read must NOT replace sentinel with ''."""

    def _make_broken_keyring(self, monkeypatch):
        import core.secrets_store as ss_module

        class _BrokenKeyring:
            def get_password(self, service, name):
                raise RuntimeError("backend unavailable")

            def set_password(self, service, name, value):
                return True

            def delete_password(self, service, name):
                pass

        monkeypatch.setattr(ss_module, "_keyring_module", lambda: _BrokenKeyring())

    def test_backend_error_preserves_sentinel_in_memory(self, monkeypatch, tmp_path):
        import json
        import core.secrets_store as ss_module

        # Write a config with a sentinel (as if keyring was working before)
        config_module.CONFIG_FILE.write_text(
            json.dumps({"hf_token": ss_module.KEYRING_SENTINEL}),
            encoding="utf-8",
        )

        self._make_broken_keyring(monkeypatch)

        loaded = Config.load()

        # Field in memory should still be the sentinel, not ""
        # (so the next save() doesn't wipe the keyring entry)
        assert loaded.hf_token == ss_module.KEYRING_SENTINEL
