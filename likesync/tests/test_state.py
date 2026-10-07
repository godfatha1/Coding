"""State store invariants."""

from __future__ import annotations

from conftest import sc, sp

from likesync.models import SOUNDCLOUD, SPOTIFY
from likesync.state import Store


def test_links_are_one_to_one(store):
    store.put_link("sp1", "sc1", score=0.9)
    assert store.counterpart(SPOTIFY, "sp1") == "sc1"
    assert store.counterpart(SOUNDCLOUD, "sc1") == "sp1"

    # Re-pointing one side must not leave the old pairing behind.
    store.put_link("sp1", "sc2", score=0.95)
    assert store.counterpart(SPOTIFY, "sp1") == "sc2"
    assert store.counterpart(SOUNDCLOUD, "sc1") is None
    assert store.count_links() == 1

    store.put_link("sp3", "sc2", score=0.99)
    assert store.counterpart(SPOTIFY, "sp1") is None
    assert store.counterpart(SOUNDCLOUD, "sc2") == "sp3"
    assert store.count_links() == 1


def test_delete_link(store):
    store.put_link("sp1", "sc1")
    assert store.delete_link(SPOTIFY, "sp1") is True
    assert store.delete_link(SPOTIFY, "sp1") is False
    assert store.counterpart(SOUNDCLOUD, "sc1") is None


def test_mirror_round_trip_and_replacement(store):
    assert store.has_mirror() is False
    store.set_mirror(SPOTIFY, ["a", "b", "c"])
    assert store.mirror(SPOTIFY) == {"a", "b", "c"}
    assert store.has_mirror() is True

    store.set_mirror(SPOTIFY, ["b"])
    assert store.mirror(SPOTIFY) == {"b"}
    # Providers must not bleed into each other.
    assert store.mirror(SOUNDCLOUD) == set()


def test_ignores(store):
    store.add_ignore(SPOTIFY, "sp1", "a 2 hour mix")
    assert store.ignored(SPOTIFY) == {"sp1"}
    assert store.remove_ignore(SPOTIFY, "sp1") is True
    assert store.ignored(SPOTIFY) == set()
    assert store.remove_ignore(SPOTIFY, "sp1") is False


def test_attempts_accumulate_tries(store):
    store.record_attempt(SPOTIFY, "sp1", status="unmatched", note="nothing close")
    store.record_attempt(SPOTIFY, "sp1", status="unmatched", note="still nothing")
    row = store.attempt(SPOTIFY, "sp1")
    assert row["tries"] == 2
    assert row["note"] == "still nothing"
    assert [r["track_id"] for r in store.attempts_by_status("unmatched")] == ["sp1"]


def test_linking_marks_both_sides_matched(store):
    store.record_attempt(SPOTIFY, "sp1", status="unmatched")
    store.put_link("sp1", "sc1", score=0.9)
    assert store.attempt(SPOTIFY, "sp1")["status"] == "matched"
    assert store.attempt(SOUNDCLOUD, "sc1")["status"] == "matched"
    assert store.attempts_by_status("unmatched") == []


def test_track_cache_round_trip(store):
    track = sp("sp1", "Archangel", ["Burial"], 237_000, isrc="GBX1")
    store.cache_track(track)
    store.db.commit()
    restored = store.cached_track(SPOTIFY, "sp1")
    assert restored == track
    assert store.describe(SPOTIFY, "sp1") == "Burial — Archangel"
    assert store.describe(SPOTIFY, "missing") == "spotify:missing"


def test_soundcloud_track_cache_keeps_extra_fields(store):
    track = sc("sc1", "Burial - Archangel", ["Hyperdub"], 237_000)
    store.cache_tracks([track])
    restored = store.cached_track(SOUNDCLOUD, "sc1")
    assert restored.raw_title == "Burial - Archangel"
    assert restored.extra["artist_source"] == "uploader"


def test_runs_and_actions(store):
    run_id = store.start_run(dry_run=False)
    store.record_action(run_id, SPOTIFY, "sp1", "like", "ok", "added on soundcloud")
    store.record_action(run_id, SPOTIFY, "sp2", "like", "failed", "nope")
    store.finish_run(run_id, status="partial", stats={"applied": 1})

    assert store.last_run()["id"] == run_id
    assert store.last_run()["status"] == "partial"
    assert len(store.actions(run_id)) == 2
    applied = store.actions(run_id, only_applied=True)
    assert [a["track_id"] for a in applied] == ["sp1"]

    store.mark_reverted(applied[0]["id"])
    assert store.actions(run_id, only_applied=True) == []


def test_meta_upsert(store):
    assert store.get_meta("missing") is None
    assert store.get_meta("missing", "fallback") == "fallback"
    store.set_meta("spotify_write_mode", "library")
    store.set_meta("spotify_write_mode", "tracks")
    assert store.get_meta("spotify_write_mode") == "tracks"


def test_schema_survives_reopen(tmp_path):
    path = tmp_path / "state.sqlite3"
    first = Store(path)
    first.put_link("sp1", "sc1")
    first.set_mirror(SPOTIFY, ["sp1"])
    first.close()

    second = Store(path)
    try:
        assert second.counterpart(SPOTIFY, "sp1") == "sc1"
        assert second.mirror(SPOTIFY) == {"sp1"}
    finally:
        second.close()
