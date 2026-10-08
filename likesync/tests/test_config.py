"""Configuration precedence and validation."""

from __future__ import annotations

import pytest

from likesync.config import load_config
from likesync.errors import ConfigError


def write(tmp_path, body):
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


def test_defaults_are_sane(tmp_path, monkeypatch):
    monkeypatch.delenv("LIKESYNC_HOME", raising=False)
    cfg = load_config(write(tmp_path, ""))
    assert cfg.sync.direction == "both"
    assert cfg.sync.propagate_unlikes is True
    assert cfg.sync.accept_threshold == 0.78
    assert cfg.soundcloud.auth_scheme == "OAuth"
    assert cfg.spotify.auth_scheme == "Bearer"


def test_toml_values_are_typed_not_stringified(tmp_path):
    cfg = load_config(write(tmp_path, """
[sync]
propagate_unlikes = false
max_unlikes_per_run = 7
accept_threshold = 0.9

[http]
timeout = 12.5
"""))
    assert cfg.sync.propagate_unlikes is False
    assert cfg.sync.max_unlikes_per_run == 7
    assert isinstance(cfg.sync.max_unlikes_per_run, int)
    assert cfg.sync.accept_threshold == 0.9
    assert cfg.http.timeout == 12.5


def test_env_overrides_the_file(tmp_path, monkeypatch):
    path = write(tmp_path, '[sync]\nmax_unlikes_per_run = 7\n')
    monkeypatch.setenv("LIKESYNC_SYNC_MAX_UNLIKES_PER_RUN", "99")
    monkeypatch.setenv("LIKESYNC_SPOTIFY_CLIENT_ID", "from-env")
    cfg = load_config(path)
    assert cfg.sync.max_unlikes_per_run == 99
    assert cfg.spotify.client_id == "from-env"


def test_unknown_keys_are_rejected_rather_than_ignored(tmp_path):
    with pytest.raises(ConfigError, match="unknown config key: sync.typo_here"):
        load_config(write(tmp_path, "[sync]\ntypo_here = 1\n"))


def test_missing_file_is_an_error_when_named_explicitly(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_validate_requires_spotify_credentials(tmp_path):
    cfg = load_config(write(tmp_path, ""))
    with pytest.raises(ConfigError, match="spotify.client_id"):
        cfg.validate()


def test_api_mode_requires_soundcloud_credentials(tmp_path):
    cfg = load_config(write(tmp_path, '[soundcloud]\nmode = "api"\n'))
    with pytest.raises(ConfigError, match="LIKESYNC_SOUNDCLOUD_CLIENT_SECRET"):
        cfg.validate()


def test_web_mode_needs_no_soundcloud_credentials(tmp_path):
    """Web mode signs in through a browser, so there is nothing to configure."""
    cfg = load_config(write(tmp_path, '[spotify]\nclient_id = "x"\n'))
    assert cfg.soundcloud.mode == "web"
    cfg.validate()


def test_bad_soundcloud_mode_is_rejected(tmp_path):
    cfg = load_config(write(tmp_path, '[soundcloud]\nmode = "telepathy"\n'))
    with pytest.raises(ConfigError, match="soundcloud.mode invalid"):
        cfg.validate()


def test_validate_accepts_a_single_provider_for_login(tmp_path):
    cfg = load_config(write(tmp_path, '[spotify]\nclient_id = "x"\n'))
    cfg.validate(providers=("spotify",))


def test_validate_rejects_a_bad_direction(tmp_path):
    cfg = load_config(write(tmp_path, """
[spotify]
client_id = "x"
[soundcloud]
mode = "api"
client_id = "y"
client_secret = "z"
[sync]
direction = "sideways"
"""))
    with pytest.raises(ConfigError, match="direction invalid"):
        cfg.validate()


def test_validate_rejects_inverted_thresholds(tmp_path):
    cfg = load_config(write(tmp_path, """
[spotify]
client_id = "x"
[soundcloud]
mode = "api"
client_id = "y"
client_secret = "z"
[sync]
accept_threshold = 0.5
review_threshold = 0.9
"""))
    with pytest.raises(ConfigError, match="review_threshold"):
        cfg.validate()


def test_bad_boolean_is_reported_with_its_key(tmp_path):
    with pytest.raises(ConfigError, match="sync.propagate_unlikes"):
        load_config(write(tmp_path, '[sync]\npropagate_unlikes = "maybe"\n'))


def test_state_and_token_paths_follow_home(tmp_path):
    cfg = load_config(write(tmp_path, f'home = "{tmp_path}"\n'))
    assert cfg.state_path == tmp_path / "state.sqlite3"
    assert cfg.token_path == tmp_path / "tokens.json"
