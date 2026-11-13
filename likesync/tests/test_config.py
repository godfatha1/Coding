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


# --- editing a config file in place -----------------------------------------

import tomllib

from likesync.config import set_config_value


def test_set_config_value_creates_the_file_and_section(tmp_path):
    path = tmp_path / "deep" / "config.toml"
    set_config_value(path, "spotify", "client_id", "abc123")
    assert tomllib.loads(path.read_text()) == {"spotify": {"client_id": "abc123"}}


def test_set_config_value_replaces_in_place_and_keeps_comments(tmp_path):
    path = write(tmp_path, """# my notes
[spotify]
# the id from Sam
client_id = "PUT_THE_SPOTIFY_CLIENT_ID_HERE"
redirect_uri = "http://127.0.0.1:8765/callback"

[sync]
direction = "both"
""")
    set_config_value(path, "spotify", "client_id", "real-id")
    text = path.read_text()
    assert "# my notes" in text and "# the id from Sam" in text
    data = tomllib.loads(text)
    assert data["spotify"]["client_id"] == "real-id"
    assert data["spotify"]["redirect_uri"].endswith("/callback")
    assert data["sync"]["direction"] == "both"
    assert text.count("client_id") == 1


def test_set_config_value_adds_a_key_to_the_right_section(tmp_path):
    path = write(tmp_path, '[spotify]\nclient_id = "x"\n\n[soundcloud]\nmode = "web"\n')
    set_config_value(path, "soundcloud", "playlist_url", "https://soundcloud.com/me/sets/in")
    data = tomllib.loads(path.read_text())
    assert data["soundcloud"] == {
        "mode": "web", "playlist_url": "https://soundcloud.com/me/sets/in"
    }
    assert "playlist_url" not in data["spotify"]


def test_set_config_value_ignores_a_commented_out_example(tmp_path):
    path = write(tmp_path, '[soundcloud]\n# playlist_url = "https://example"\nmode = "web"\n')
    set_config_value(path, "soundcloud", "playlist_url", "https://real")
    text = path.read_text()
    assert '# playlist_url = "https://example"' in text
    assert tomllib.loads(text)["soundcloud"]["playlist_url"] == "https://real"


def test_set_config_value_writes_typed_values_and_escapes_strings(tmp_path):
    path = tmp_path / "c.toml"
    set_config_value(path, "soundcloud", "headless", False)
    set_config_value(path, "sync", "max_unlikes_per_run", 7)
    set_config_value(path, "soundcloud", "browser_executable", 'C:\\Program Files\\x "y"')
    data = tomllib.loads(path.read_text())
    assert data["soundcloud"]["headless"] is False
    assert data["sync"]["max_unlikes_per_run"] == 7
    assert data["soundcloud"]["browser_executable"] == 'C:\\Program Files\\x "y"'


def test_edited_file_still_loads_as_a_config(tmp_path):
    path = tmp_path / "c.toml"
    set_config_value(path, "spotify", "client_id", "abc")
    set_config_value(path, "soundcloud", "mode", "web")
    cfg = load_config(path)
    assert cfg.spotify.client_id == "abc"
    cfg.validate()
