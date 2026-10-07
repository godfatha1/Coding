"""OAuth flow mechanics."""

from __future__ import annotations

import base64
import hashlib
import time
import urllib.parse

import pytest

from likesync.errors import AuthError
from likesync.httpc import ApiClient, FakeTransport, Response, json_response
from likesync.oauth import Authenticator, OAuthEndpoints, pkce_pair
from likesync.tokens import TokenSet, TokenStore

AUTH_BASE = "https://secure.example.com"


def build(tmp_path, routes, *, include_secret=False, scheme="Bearer"):
    transport = FakeTransport(routes)
    client = ApiClient(base_url=AUTH_BASE, transport=transport, name="test-auth",
                       sleeper=lambda _: None)
    store = TokenStore(tmp_path / "tokens.json")
    auth = Authenticator(
        name="spotify",
        client_id="client-123",
        client_secret="secret-456",
        redirect_uri="http://127.0.0.1:8765/callback",
        scopes="user-library-read user-library-modify",
        endpoints=OAuthEndpoints(
            authorize=f"{AUTH_BASE}/authorize", token=f"{AUTH_BASE}/oauth/token"
        ),
        store=store,
        client=client,
        include_secret=include_secret,
        auth_scheme=scheme,
    )
    return auth, transport, store


def test_pkce_pair_is_a_valid_s256_challenge():
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode()
    assert challenge == expected
    assert "=" not in challenge
    # Fresh randomness every call.
    assert pkce_pair()[0] != verifier


def test_authorize_url_carries_pkce_and_scopes(tmp_path):
    auth, _, _ = build(tmp_path, {})
    url = auth.build_authorize_url("state-xyz", "challenge-abc")
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert url.startswith(f"{AUTH_BASE}/authorize?")
    assert query["client_id"] == "client-123"
    assert query["response_type"] == "code"
    assert query["code_challenge"] == "challenge-abc"
    assert query["code_challenge_method"] == "S256"
    assert query["state"] == "state-xyz"
    assert query["redirect_uri"] == "http://127.0.0.1:8765/callback"
    assert "user-library-modify" in query["scope"]


def test_exchange_sends_pkce_verifier_and_omits_secret_by_default(tmp_path):
    auth, transport, _ = build(tmp_path, {
        "POST /oauth/token": json_response(
            {"access_token": "at", "refresh_token": "rt", "expires_in": 3600,
             "scope": "user-library-read"}
        )
    })
    tokens = auth.exchange("the-code", "the-verifier")
    body = dict(urllib.parse.parse_qsl(transport.calls[0]["body"].decode()))

    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "the-code"
    assert body["code_verifier"] == "the-verifier"
    assert body["client_id"] == "client-123"
    assert "client_secret" not in body      # PKCE must not send one
    assert tokens.access_token == "at"
    assert tokens.expires_at > time.time()


def test_exchange_includes_secret_for_confidential_clients(tmp_path):
    """SoundCloud treats every client as confidential, even with PKCE."""
    auth, transport, _ = build(tmp_path, {
        "POST /oauth/token": json_response({"access_token": "at", "expires_in": 3600})
    }, include_secret=True)
    auth.exchange("code", "verifier")
    body = dict(urllib.parse.parse_qsl(transport.calls[0]["body"].decode()))
    assert body["client_secret"] == "secret-456"


def test_refresh_reports_invalid_grant_as_needing_a_new_login(tmp_path):
    auth, _, _ = build(tmp_path, {
        "POST /oauth/token": Response(
            status=400, body=b'{"error":"invalid_grant","error_description":"expired"}'
        )
    })
    with pytest.raises(AuthError, match="likesync login spotify"):
        auth.refresh(TokenSet(refresh_token="stale"))


def test_refresh_without_a_refresh_token_is_an_auth_error(tmp_path):
    auth, _, _ = build(tmp_path, {})
    with pytest.raises(AuthError, match="no refresh token"):
        auth.refresh(TokenSet(access_token="only-access"))


def test_refresh_now_persists_the_rotated_token(tmp_path):
    auth, transport, store = build(tmp_path, {
        "POST /oauth/token": json_response(
            {"access_token": "new-at", "refresh_token": "rotated", "expires_in": 3600}
        )
    })
    store.save("spotify", TokenSet(access_token="old", refresh_token="original",
                                   expires_at=time.time() - 10))
    assert auth.refresh_now() is True
    # Persisted immediately: a lost write would strand a single-use token.
    assert store.load("spotify").refresh_token == "rotated"
    assert auth.access_token() == "new-at"


def test_refresh_now_adopts_a_token_another_process_already_refreshed(tmp_path):
    auth, transport, store = build(tmp_path, {
        "POST /oauth/token": Response(status=500, body=b"should not be called")
    })
    # Our cached copy is stale, but the file already holds a fresh token.
    auth._cached = TokenSet(access_token="stale", refresh_token="r",
                            expires_at=time.time() - 10)
    store.save("spotify", TokenSet(access_token="fresh-from-elsewhere",
                                   refresh_token="r2",
                                   expires_at=time.time() + 3600))
    assert auth.refresh_now() is True
    assert auth.access_token() == "fresh-from-elsewhere"
    assert transport.calls == [], "must not spend a single-use refresh token"


def test_access_token_without_credentials_tells_you_what_to_run(tmp_path):
    auth, _, _ = build(tmp_path, {})
    with pytest.raises(AuthError, match="likesync login spotify"):
        auth.access_token()


def test_header_uses_the_configured_scheme(tmp_path):
    auth, _, store = build(tmp_path, {}, scheme="OAuth")
    store.save("spotify", TokenSet(access_token="tok", expires_at=time.time() + 3600))
    assert auth.header() == {"Authorization": "OAuth tok"}


def test_login_rejects_a_mismatched_state(tmp_path, monkeypatch):
    auth, _, _ = build(tmp_path, {
        "POST /oauth/token": json_response({"access_token": "at"})
    })
    monkeypatch.setattr(
        "builtins.input",
        lambda *_: "http://127.0.0.1:8765/callback?code=c&state=attacker",
    )
    with pytest.raises(AuthError, match="state mismatch"):
        auth.login(manual=True, open_browser=False)


def test_login_surfaces_a_provider_error(tmp_path, monkeypatch):
    auth, _, _ = build(tmp_path, {})
    monkeypatch.setattr(
        "builtins.input",
        lambda *_: "http://127.0.0.1:8765/callback?error=access_denied"
                   "&error_description=User+said+no",
    )
    with pytest.raises(AuthError, match="access_denied"):
        auth.login(manual=True, open_browser=False)


def test_manual_login_stores_tokens(tmp_path, monkeypatch):
    auth, _, store = build(tmp_path, {
        "POST /oauth/token": json_response(
            {"access_token": "at", "refresh_token": "rt", "expires_in": 3600}
        )
    })
    captured = {}

    def fake_input(*_):
        # Echo back the state the authorize URL asked for.
        return f"http://127.0.0.1:8765/callback?code=c&state={captured['state']}"

    original = auth.build_authorize_url

    def spy(state, challenge):
        captured["state"] = state
        return original(state, challenge)

    auth.build_authorize_url = spy
    monkeypatch.setattr("builtins.input", fake_input)
    tokens = auth.login(manual=True, open_browser=False)
    assert tokens.access_token == "at"
    assert store.load("spotify").refresh_token == "rt"
