"""Token persistence, refresh semantics and the file lock."""

from __future__ import annotations

import base64
import json
import os
import time

import pytest

from likesync.errors import AuthError, ConfigError
from likesync.tokens import ENV_SECRET_KEY, ENV_SEED, TokenSet, TokenStore


def test_token_set_round_trip(tmp_path):
    store = TokenStore(tmp_path / "tokens.json")
    store.save("spotify", TokenSet(access_token="a", refresh_token="r",
                                   expires_at=time.time() + 3600, scope="s"))
    loaded = store.load("spotify")
    assert loaded.access_token == "a"
    assert loaded.refresh_token == "r"
    assert loaded.expired is False
    assert store.load("soundcloud").usable is False


def test_token_file_is_owner_only(tmp_path):
    path = tmp_path / "tokens.json"
    TokenStore(path).save("spotify", TokenSet(access_token="a"))
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_expiry_margin_refreshes_early():
    nearly = TokenSet(access_token="a", expires_at=time.time() + 30)
    assert nearly.expired is True
    comfortable = TokenSet(access_token="a", expires_at=time.time() + 600)
    assert comfortable.expired is False


def test_refresh_keeps_the_old_refresh_token_when_none_is_returned():
    """Spotify's PKCE refresh may omit refresh_token; the old one stays valid."""
    previous = TokenSet(access_token="old", refresh_token="keep-me",
                        scope="user-library-read", consent_at=1000.0)
    refreshed = TokenSet.from_response(
        {"access_token": "new", "expires_in": 3600}, previous=previous
    )
    assert refreshed.refresh_token == "keep-me"
    assert refreshed.access_token == "new"
    assert refreshed.scope == "user-library-read"
    # Consent time anchors Spotify's six-month refresh-token lifetime, so an
    # ordinary refresh must not reset it.
    assert refreshed.consent_at == 1000.0


def test_refresh_adopts_a_rotated_refresh_token():
    """SoundCloud invalidates the old token on every refresh."""
    previous = TokenSet(access_token="old", refresh_token="spent")
    refreshed = TokenSet.from_response(
        {"access_token": "new", "refresh_token": "fresh", "expires_in": 3600},
        previous=previous,
    )
    assert refreshed.refresh_token == "fresh"


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "tokens.json"
    store = TokenStore(path)
    store.save("spotify", TokenSet(access_token="a", refresh_token="r"))
    store.save("soundcloud", TokenSet(access_token="b", refresh_token="r2"))
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["tokens.json"]
    assert set(store.load_all()) == {"spotify", "soundcloud"}


def test_forget_one_provider(tmp_path):
    store = TokenStore(tmp_path / "tokens.json")
    store.save("spotify", TokenSet(access_token="a"))
    store.save("soundcloud", TokenSet(access_token="b"))
    assert store.forget("spotify") is True
    assert store.forget("spotify") is False
    assert set(store.load_all()) == {"soundcloud"}


def test_lock_is_reentrant_across_sequential_use(tmp_path):
    store = TokenStore(tmp_path / "tokens.json")
    with store.locked():
        store.save("spotify", TokenSet(access_token="a"))
    with store.locked():
        assert store.load("spotify").access_token == "a"


def test_seed_from_environment(tmp_path, monkeypatch):
    payload = {"version": 1,
               "providers": {"spotify": {"access_token": "seeded",
                                         "refresh_token": "r"}}}
    blob = base64.b64encode(json.dumps(payload).encode()).decode()
    monkeypatch.setenv(ENV_SEED, blob)
    store = TokenStore(tmp_path / "missing.json")
    assert store.load("spotify").access_token == "seeded"


def test_export_round_trips_through_the_seed(tmp_path, monkeypatch):
    store = TokenStore(tmp_path / "tokens.json")
    store.save("spotify", TokenSet(access_token="a", refresh_token="r"))
    blob = store.export_b64()

    monkeypatch.setenv(ENV_SEED, blob)
    fresh = TokenStore(tmp_path / "elsewhere.json")
    assert fresh.load("spotify").refresh_token == "r"


def test_bad_secret_key_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_SECRET_KEY, base64.urlsafe_b64encode(b"short").decode())
    with pytest.raises(ConfigError, match="16, 24 or 32 bytes"):
        TokenStore(tmp_path / "tokens.json").save("spotify", TokenSet(access_token="a"))


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("cryptography"),
    reason="cryptography not installed",
)
def test_encrypted_store_round_trip(tmp_path, monkeypatch):
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv(ENV_SECRET_KEY, key)
    path = tmp_path / "tokens.json"
    store = TokenStore(path)
    store.save("spotify", TokenSet(access_token="secret-value", refresh_token="r"))

    # The plaintext must not be readable from the file.
    raw = path.read_text()
    assert "secret-value" not in raw
    assert json.loads(raw)["enc"] == "aes-gcm"
    assert TokenStore(path).load("spotify").access_token == "secret-value"

    # A wrong key must fail loudly rather than silently losing tokens.
    monkeypatch.setenv(ENV_SECRET_KEY,
                       base64.urlsafe_b64encode(os.urandom(32)).decode())
    with pytest.raises(AuthError, match="could not decrypt"):
        TokenStore(path).load("spotify")


def test_consent_age_days():
    assert TokenSet().consent_age_days() is None
    aged = TokenSet(consent_at=time.time() - 86400 * 10)
    assert 9.9 < aged.consent_age_days() < 10.1
