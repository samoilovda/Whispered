"""The real-Qt suite runs with the project venv, where ``keyring`` is
installed: saving a test Config must never reach the developer's real
OS keyring (it once deleted the real YouTube OAuth client secret)."""

import keyring


def test_config_save_stays_in_the_in_memory_keyring():
    from config import Config

    assert not hasattr(keyring, "get_keyring"), "the real keyring is loaded"
    keyring.set_password("Whispered", "yt_oauth_client_secret", "kept")
    Config().save()                      # empty secret fields: deletes entries
    assert keyring.get_password("Whispered", "yt_oauth_client_secret") is None
