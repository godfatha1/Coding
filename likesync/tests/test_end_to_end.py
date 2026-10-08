"""Full stack: config -> auth -> providers -> engine, over a faked network."""

from __future__ import annotations

import json
import time

from likesync.app import build_app
from likesync.cli import main
from likesync.httpc import FakeTransport, json_response
from likesync.tokens import TokenSet, TokenStore

SP_TRACK = {
    "id": "spA",
    "name": "Midnight City",
    "artists": [{"name": "M83"}],
    "duration_ms": 244_000,
    "external_ids": {"isrc": "USX1"},
    "external_urls": {"spotify": "https://open.spotify.com/track/spA"},
    "album": {"name": "Hurry Up"},
}
SC_TRACK = {
    "kind": "track",
    "id": 55,
    "urn": "soundcloud:tracks:55",
    "title": "Burial - Archangel",
    "user": {"username": "Hyperdub"},
    "full_duration": 237_000,
    "permalink_url": "https://soundcloud.com/hyperdub/archangel",
    "publisher_metadata": None,
}
# The counterpart each side should find for the other's track.
SC_MATCH = {
    "kind": "track",
    "id": 99,
    "urn": "soundcloud:tracks:99",
    "title": "M83 - Midnight City",
    "user": {"username": "Indie Blog"},
    "full_duration": 243_000,
    "permalink_url": "https://soundcloud.com/indie/midnight-city",
    "publisher_metadata": None,
}
SP_MATCH = {
    "id": "spB",
    "name": "Archangel",
    "artists": [{"name": "Burial"}],
    "duration_ms": 236_400,
    "external_ids": {},
    "external_urls": {"spotify": "https://open.spotify.com/track/spB"},
    "album": {"name": "Untrue"},
}


def seed_config(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f"""
home = "{tmp_path}"

[spotify]
client_id = "sp-client"

[soundcloud]
mode = "api"
client_id = "sc-client"
client_secret = "sc-secret"

[sync]
max_searches_per_run = 50
""")
    store = TokenStore(tmp_path / "tokens.json")
    for provider in ("spotify", "soundcloud"):
        store.save(provider, TokenSet(access_token=f"{provider}-token",
                                      refresh_token="r",
                                      expires_at=time.time() + 3600))
    return cfg_path


def routes():
    writes = {"spotify": [], "soundcloud": []}

    def sp_library(req):
        writes["spotify"].append(req["query"]["uris"])
        return json_response({})

    def sc_like(req):
        writes["soundcloud"].append(req["path"])
        return json_response({})

    table = {
        "GET /v1/me/tracks": json_response({"items": [{"added_at": "2026-01-01T00:00:00Z",
                                                       "track": SP_TRACK}],
                                            "next": None}),
        "GET /me/likes/tracks": json_response({"collection": [SC_TRACK],
                                               "next_href": None}),
        "GET /v1/search": json_response({"tracks": {"items": [SP_MATCH]}}),
        "GET /tracks": json_response({"collection": [SC_MATCH]}),
        "PUT /v1/me/library": sp_library,
        "POST /likes/tracks/*": sc_like,
    }
    return table, writes


def test_full_sync_merges_both_libraries(tmp_path):
    from likesync.config import load_config

    cfg_path = seed_config(tmp_path)
    table, writes = routes()
    transport = FakeTransport(table)

    cfg = load_config(cfg_path)
    app = build_app(cfg, transport=transport)
    try:
        report = app.engine().run()
    finally:
        app.close()

    assert report.aborted is None
    assert report.failed == []
    # Each side gained the other's track.
    assert writes["spotify"] == ["spotify:track:spB"]
    assert writes["soundcloud"] == ["/likes/tracks/99"]
    assert report.new_links == 2

    # Auth headers reflect each provider's scheme.
    sp_call = next(c for c in transport.calls if c["path"] == "/v1/me/tracks")
    sc_call = next(c for c in transport.calls if c["path"] == "/me/likes/tracks")
    assert sp_call["headers"]["Authorization"] == "Bearer spotify-token"
    assert sc_call["headers"]["Authorization"] == "OAuth soundcloud-token"


def test_dry_run_through_the_full_stack_writes_nothing(tmp_path):
    from likesync.config import load_config

    cfg_path = seed_config(tmp_path)
    table, writes = routes()
    cfg = load_config(cfg_path)
    app = build_app(cfg, transport=FakeTransport(table))
    try:
        report = app.engine().run(dry_run=True)
    finally:
        app.close()

    assert len(report.planned) == 2
    assert writes == {"spotify": [], "soundcloud": []}


def test_second_run_is_idempotent(tmp_path):
    from likesync.config import load_config

    cfg_path = seed_config(tmp_path)
    table, writes = routes()
    cfg = load_config(cfg_path)

    app = build_app(cfg, transport=FakeTransport(table))
    try:
        app.engine().run()
    finally:
        app.close()

    # Both libraries now hold both tracks.
    table["GET /v1/me/tracks"] = json_response(
        {"items": [{"track": SP_TRACK}, {"track": SP_MATCH}], "next": None}
    )
    table["GET /me/likes/tracks"] = json_response(
        {"collection": [SC_TRACK, SC_MATCH], "next_href": None}
    )
    writes["spotify"].clear()
    writes["soundcloud"].clear()

    app2 = build_app(load_config(cfg_path), transport=FakeTransport(table))
    try:
        report = app2.engine().run()
    finally:
        app2.close()

    assert report.planned == []
    assert writes == {"spotify": [], "soundcloud": []}


def test_cli_status_runs_offline(tmp_path, capsys):
    cfg_path = seed_config(tmp_path)
    assert main(["--config", str(cfg_path), "status"]) == 0
    out = capsys.readouterr().out
    assert "spotify" in out and "connected" in out
    assert "links" in out


def test_cli_reports_missing_credentials_clearly(tmp_path, capsys):
    cfg_path = tmp_path / "bare.toml"
    cfg_path.write_text(f'home = "{tmp_path}"\n')
    code = main(["--config", str(cfg_path), "sync"])
    assert code == 2
    assert "spotify.client_id" in capsys.readouterr().err


def test_cli_link_and_unlink_round_trip(tmp_path, capsys):
    cfg_path = seed_config(tmp_path)
    assert main(["--config", str(cfg_path), "link", "spotify", "spA", "55"]) == 0
    assert main(["--config", str(cfg_path), "unmatched"]) == 0
    assert main(["--config", str(cfg_path), "unlink", "spotify", "spA"]) == 0
    assert main(["--config", str(cfg_path), "unlink", "spotify", "spA"]) == 1


def test_cli_ignore_round_trip(tmp_path):
    cfg_path = seed_config(tmp_path)
    assert main(["--config", str(cfg_path), "ignore", "soundcloud", "55",
                 "--reason", "two hour mix"]) == 0
    assert main(["--config", str(cfg_path), "unignore", "soundcloud", "55"]) == 0
    assert main(["--config", str(cfg_path), "unignore", "soundcloud", "55"]) == 1


def test_cli_tokens_export(tmp_path, capsys):
    cfg_path = seed_config(tmp_path)
    assert main(["--config", str(cfg_path), "tokens", "export"]) == 0
    blob = capsys.readouterr().out.strip()
    import base64
    payload = json.loads(base64.b64decode(blob))
    assert set(payload["providers"]) == {"spotify", "soundcloud"}


# --------------------------------------------------------------------------
# web-mode CLI surface
# --------------------------------------------------------------------------


def web_config(tmp_path, extra: str = "") -> object:
    cfg_path = tmp_path / "web.toml"
    cfg_path.write_text(f"""
home = "{tmp_path}"

[spotify]
client_id = "sp-client"

[soundcloud]
mode = "web"
{extra}
""")
    return cfg_path


def test_session_path_is_reported(tmp_path, capsys):
    cfg_path = web_config(tmp_path)
    assert main(["--config", str(cfg_path), "session", "path"]) == 0
    assert "soundcloud-session.json" in capsys.readouterr().out


def test_session_export_without_a_session_is_an_error(tmp_path, capsys):
    cfg_path = web_config(tmp_path)
    assert main(["--config", str(cfg_path), "session", "export"]) == 1
    assert "No saved session" in capsys.readouterr().err


def test_session_export_import_round_trip(tmp_path, capsys, monkeypatch):
    cfg_path = web_config(tmp_path)
    session_file = tmp_path / "soundcloud-session.json"
    session_file.write_text(json.dumps({"cookies": [{"name": "x"}], "origins": []}))

    assert main(["--config", str(cfg_path), "session", "export"]) == 0
    blob = capsys.readouterr().out.strip()

    session_file.unlink()
    monkeypatch.setenv("LIKESYNC_SESSION", blob)
    assert main(["--config", str(cfg_path), "session", "import"]) == 0
    assert json.loads(session_file.read_text())["cookies"][0]["name"] == "x"
    # Session files hold a live sign-in, so they must not be world readable.
    assert oct(session_file.stat().st_mode & 0o777) == "0o600"


def test_session_import_without_the_env_var_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.delenv("LIKESYNC_SESSION", raising=False)
    cfg_path = web_config(tmp_path)
    assert main(["--config", str(cfg_path), "session", "import"]) == 2


def test_session_forget_removes_the_sign_in(tmp_path, capsys):
    cfg_path = web_config(tmp_path)
    session_file = tmp_path / "soundcloud-session.json"
    session_file.write_text("{}")
    assert main(["--config", str(cfg_path), "session", "forget"]) == 0
    assert not session_file.exists()
    assert main(["--config", str(cfg_path), "session", "forget"]) == 0


def test_status_reports_the_browser_session(tmp_path, capsys):
    cfg_path = web_config(
        tmp_path, 'playlist_url = "https://soundcloud.com/me/sets/sync-me"'
    )
    (tmp_path / "soundcloud-session.json").write_text("{}")
    assert main(["--config", str(cfg_path), "status"]) == 0
    out = capsys.readouterr().out
    assert "browser session saved" in out
    assert "inbox" in out and "sets/sync-me" in out


def test_status_says_when_the_browser_session_is_missing(tmp_path, capsys):
    cfg_path = web_config(tmp_path)
    assert main(["--config", str(cfg_path), "status"]) == 0
    assert "login soundcloud --web" in capsys.readouterr().out


def test_reseed_is_a_no_op_when_nothing_was_taken_in(tmp_path, capsys):
    cfg_path = web_config(tmp_path)
    assert main(["--config", str(cfg_path), "reseed", "--yes"]) == 0
    assert "nothing recorded" in capsys.readouterr().out


def test_reseed_clears_the_inbox_memory(tmp_path, capsys):
    from likesync.config import load_config
    from likesync.state import Store

    cfg_path = web_config(tmp_path)
    cfg = load_config(cfg_path)
    store = Store(cfg.state_path)
    store.mark_playlist_seen("soundcloud", ["a", "b", "c"])
    store.close()

    assert main(["--config", str(cfg_path), "reseed", "--yes"]) == 0
    assert "Cleared 3" in capsys.readouterr().out

    store = Store(cfg.state_path)
    assert store.playlist_seen("soundcloud") == set()
    store.close()


def test_probe_says_so_when_api_mode_has_none(tmp_path, capsys):
    cfg_path = seed_config(tmp_path)  # api mode
    assert main(["--config", str(cfg_path), "probe", "soundcloud"]) == 0
    assert "no probe" in capsys.readouterr().out


def test_web_mode_does_not_launch_a_browser_just_to_report_status(tmp_path):
    """Constructing the providers must not spawn Chromium."""
    from likesync.app import build_app
    from likesync.config import load_config

    cfg = load_config(web_config(tmp_path))
    app = build_app(cfg)
    try:
        assert app.web_session is not None
        assert app.web_session._browser is None, "browser launched too eagerly"
    finally:
        app.close()
