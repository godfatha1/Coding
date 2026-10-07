"""Provider HTTP behaviour, including the endpoint fallbacks."""

from __future__ import annotations

import json

import pytest

from likesync.errors import ProviderError
from likesync.httpc import ApiClient, FakeTransport, Response, json_response
from likesync.providers.base import DictMemo
from likesync.providers.soundcloud import SoundCloudProvider, path_id
from likesync.providers.spotify import SpotifyProvider

SP_BASE = "https://api.spotify.com/v1"
SC_BASE = "https://api.soundcloud.com"


class StubAuth:
    def header(self):
        return {"Authorization": "Bearer test-token"}

    def refresh_now(self):
        return False


def build_spotify(routes, *, write_mode="auto", memo=None):
    transport = FakeTransport(routes)
    auth = StubAuth()
    client = ApiClient(
        base_url=SP_BASE, transport=transport, name="spotify",
        auth_header=auth.header, on_unauthorized=auth.refresh_now,
        sleeper=lambda _: None,
    )
    return SpotifyProvider(client, auth, memo=memo or DictMemo(),
                           write_mode=write_mode), transport


def build_soundcloud(routes, *, write_mode="auto", memo=None):
    transport = FakeTransport(routes)
    auth = StubAuth()
    client = ApiClient(
        base_url=SC_BASE, transport=transport, name="soundcloud",
        auth_header=auth.header, on_unauthorized=auth.refresh_now,
        sleeper=lambda _: None,
    )
    return SoundCloudProvider(client, auth, memo=memo or DictMemo(),
                              write_mode=write_mode), transport


# --------------------------------------------------------------------------
# Spotify
# --------------------------------------------------------------------------


def spotify_track(track_id, name="Song", artist="Artist", ms=200_000, isrc="USX000"):
    return {
        "id": track_id,
        "name": name,
        "artists": [{"name": artist}],
        "duration_ms": ms,
        "external_ids": {"isrc": isrc},
        "external_urls": {"spotify": f"https://open.spotify.com/track/{track_id}"},
        "album": {"name": "An Album"},
    }


def test_spotify_liked_paginates_and_skips_local_files():
    page2 = f"{SP_BASE}/me/tracks?offset=50"

    def handler(req):
        if req["query"].get("offset") == "50":
            return json_response({
                "items": [{"added_at": "2026-01-02T00:00:00Z",
                           "track": spotify_track("t3")}],
                "next": None,
            })
        return json_response({
            "items": [
                {"added_at": "2026-01-01T00:00:00Z", "track": spotify_track("t1")},
                # A local file: no id, unaddressable by the API.
                {"added_at": "2026-01-01T00:00:00Z",
                 "track": {"id": None, "is_local": True, "name": "Local"}},
                {"added_at": "2026-01-01T00:00:00Z", "track": spotify_track("t2")},
            ],
            "next": page2,
        })

    provider, transport = build_spotify({"GET /v1/me/tracks": handler})
    tracks = provider.liked()

    assert [t.id for t in tracks] == ["t1", "t2", "t3"]
    assert tracks[0].isrc == "USX000"
    assert tracks[0].added_at == "2026-01-01T00:00:00Z"
    assert len(transport.calls) == 2


def test_spotify_liked_raises_rather_than_returning_a_partial_library():
    # A truncated library would read as a mass unlike, so a mid-pagination
    # failure must raise.
    def handler(req):
        if req["query"].get("offset") == "50":
            return Response(status=500, body=b"boom")
        return json_response({"items": [{"track": spotify_track("t1")}],
                              "next": f"{SP_BASE}/me/tracks?offset=50"})

    provider, _ = build_spotify({"GET /v1/me/tracks": handler})
    with pytest.raises(ProviderError):
        provider.liked()


def test_spotify_like_uses_unified_library_endpoint():
    provider, transport = build_spotify(
        {"PUT /v1/me/library": json_response({})}
    )
    assert provider.like(["a", "b"]) == ["a", "b"]

    call = transport.calls[0]
    assert call["method"] == "PUT"
    # uris travel as a comma-separated query parameter, not a JSON body.
    assert call["query"]["uris"] == "spotify:track:a,spotify:track:b"
    assert call["body"] is None
    assert "spotify%3Atrack%3Aa" in call["url"]


def test_spotify_like_batches_at_forty():
    seen = []

    def handler(req):
        seen.append(req["query"]["uris"].split(","))
        return json_response({})

    provider, _ = build_spotify({"PUT /v1/me/library": handler})
    ids = [f"id{i}" for i in range(95)]
    assert provider.like(ids) == ids
    assert [len(batch) for batch in seen] == [40, 40, 15]


def test_spotify_falls_back_to_legacy_tracks_endpoint_on_403():
    memo = DictMemo()
    provider, transport = build_spotify(
        {
            "PUT /v1/me/library": Response(status=403, body=b'{"error":"forbidden"}'),
            "PUT /v1/me/tracks": json_response({}),
        },
        memo=memo,
    )
    assert provider.like(["a"]) == ["a"]

    assert [c["path"] for c in transport.calls] == [
        "/v1/me/library", "/v1/me/tracks"
    ]
    assert json.loads(transport.calls[1]["body"]) == {"ids": ["a"]}
    # The working variant is remembered, so later runs skip the dead probe.
    assert memo.get_meta("spotify_write_mode") == "tracks"

    provider2, transport2 = build_spotify({"PUT /v1/me/tracks": json_response({})},
                                          memo=memo)
    provider2.like(["b"])
    assert [c["path"] for c in transport2.calls] == ["/v1/me/tracks"]


def test_spotify_isolates_one_bad_id_from_a_batch():
    def handler(req):
        uris = req["query"]["uris"].split(",")
        if len(uris) > 1:
            return Response(status=400, body=b'{"error":"bad id"}')
        if uris[0].endswith("bad"):
            return Response(status=400, body=b'{"error":"bad id"}')
        return json_response({})

    provider, _ = build_spotify({"PUT /v1/me/library": handler},
                                write_mode="library")
    done = provider.like(["good1", "bad", "good2"])
    assert sorted(done) == ["good1", "good2"]
    assert "bad" in provider.failures


def test_spotify_unlike_uses_delete():
    provider, transport = build_spotify(
        {"DELETE /v1/me/library": json_response({})}
    )
    assert provider.unlike(["x"]) == ["x"]
    assert transport.calls[0]["method"] == "DELETE"


def test_spotify_search_by_isrc():
    def handler(req):
        assert req["query"]["q"] == "isrc:USX000"
        return json_response({"tracks": {"items": [spotify_track("t1")]}})

    provider, _ = build_spotify({"GET /v1/search": handler})
    assert [t.id for t in provider.search_isrc("USX000")] == ["t1"]


def test_spotify_retries_on_429_then_succeeds():
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            return Response(status=429, headers={"retry-after": "1"}, body=b"slow down")
        return json_response({"items": [{"track": spotify_track("t1")}], "next": None})

    provider, transport = build_spotify({"GET /v1/me/tracks": handler})
    assert [t.id for t in provider.liked()] == ["t1"]
    assert len(transport.calls) == 2


# --------------------------------------------------------------------------
# SoundCloud
# --------------------------------------------------------------------------


def sc_track(numeric_id, title="Artist - Song", username="Uploader", ms=200_000,
             isrc=None, artist=None):
    return {
        "kind": "track",
        "id": numeric_id,
        "urn": f"soundcloud:tracks:{numeric_id}",
        "title": title,
        "user": {"username": username},
        "duration": 30_000,          # preview length
        "full_duration": ms,         # the real length
        "permalink_url": f"https://soundcloud.com/x/{numeric_id}",
        "publisher_metadata": ({"isrc": isrc, "artist": artist}
                               if (isrc or artist) else None),
    }


def test_soundcloud_liked_follows_linked_partitioning():
    page2 = f"{SC_BASE}/me/likes/tracks?cursor=abc"

    def handler(req):
        if req["query"].get("cursor") == "abc":
            return json_response({"collection": [sc_track(3)], "next_href": None})
        return json_response({
            "collection": [sc_track(1), sc_track(2)],
            "next_href": page2,
        })

    provider, transport = build_soundcloud({"GET /me/likes/tracks": handler})
    tracks = provider.liked()

    assert [t.id for t in tracks] == [
        "soundcloud:tracks:1", "soundcloud:tracks:2", "soundcloud:tracks:3"
    ]
    # full_duration wins over the 30s preview duration.
    assert tracks[0].duration_ms == 200_000
    assert len(transport.calls) == 2


def test_soundcloud_prefers_publisher_metadata_for_artist_and_isrc():
    provider, _ = build_soundcloud({
        "GET /me/likes/tracks": json_response({
            "collection": [sc_track(1, artist="Real Artist", isrc="GBX123",
                                    username="Promo Channel")],
            "next_href": None,
        })
    })
    track = provider.liked()[0]
    assert track.artists[0] == "Real Artist"
    assert track.isrc == "GBX123"
    assert track.extra["artist_source"] == "publisher"


def test_soundcloud_marks_uploader_only_artists():
    provider, _ = build_soundcloud({
        "GET /me/likes/tracks": json_response({
            "collection": [sc_track(1, username="Promo Channel")],
            "next_href": None,
        })
    })
    assert provider.liked()[0].extra["artist_source"] == "uploader"


def test_soundcloud_like_uses_numeric_path_for_a_urn():
    provider, transport = build_soundcloud(
        {"POST /likes/tracks/1234": json_response({})}
    )
    assert provider.like(["soundcloud:tracks:1234"]) == ["soundcloud:tracks:1234"]
    assert transport.calls[0]["path"] == "/likes/tracks/1234"


def test_soundcloud_falls_back_to_favorites_on_405():
    memo = DictMemo()
    provider, transport = build_soundcloud(
        {
            "POST /likes/tracks/7": Response(status=405, body=b"not allowed"),
            "PUT /me/favorites/7": json_response({}),
        },
        memo=memo,
    )
    assert provider.like(["7"]) == ["7"]
    assert [c["path"] for c in transport.calls] == [
        "/likes/tracks/7", "/me/favorites/7"
    ]
    assert memo.get_meta("soundcloud_write_mode") == "favorites"


def test_soundcloud_unlike_treats_404_as_already_gone():
    provider, _ = build_soundcloud(
        {"DELETE /likes/tracks/9": Response(status=404, body=b"not found")}
    )
    assert provider.unlike(["9"]) == ["9"]


def test_soundcloud_per_track_failure_does_not_stop_the_batch():
    def handler(req):
        if req["path"].endswith("/2"):
            return Response(status=500, body=b"nope")
        return json_response({})

    provider, _ = build_soundcloud(
        {
            "POST /likes/tracks/1": handler,
            "POST /likes/tracks/2": handler,
            "POST /likes/tracks/3": handler,
        },
        write_mode="likes",
    )
    assert provider.like(["1", "2", "3"]) == ["1", "3"]
    assert "2" in provider.failures


def test_soundcloud_search_reads_collection():
    provider, _ = build_soundcloud(
        {"GET /tracks": json_response({"collection": [sc_track(5)]})}
    )
    assert [t.id for t in provider.search("query")] == ["soundcloud:tracks:5"]


@pytest.mark.parametrize("raw,expected", [
    ("soundcloud:tracks:12345", "12345"),
    ("12345", "12345"),
    ("soundcloud:tracks:abc-def", "soundcloud:tracks:abc-def"),
])
def test_path_id_handles_urn_and_numeric_forms(raw, expected):
    assert path_id(raw) == expected
