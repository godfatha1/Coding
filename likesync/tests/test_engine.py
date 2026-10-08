"""Reconciliation behaviour: the part that must never delete a library."""

from __future__ import annotations

from conftest import FakeProvider, sc, sp

from likesync.engine import LIKE, UNLIKE, SyncEngine
from likesync.models import SOUNDCLOUD, SPOTIFY

MIDNIGHT_SP = sp("sp1", "Midnight City", ["M83"], 244_000)
MIDNIGHT_SC = sc("sc1", "M83 - Midnight City", ["Indie Blog"], 243_000)
ARCHANGEL_SC = sc("sc2", "Burial - Archangel", ["Hyperdub"], 237_000)
ARCHANGEL_SP = sp("sp2", "Archangel", ["Burial"], 236_500)


def make(store, cfg, *, spotify_lib=(), soundcloud_lib=(),
         spotify_catalog=(), soundcloud_catalog=()):
    spotify = FakeProvider(SPOTIFY, spotify_lib, spotify_catalog)
    soundcloud = FakeProvider(SOUNDCLOUD, soundcloud_lib, soundcloud_catalog)
    engine = SyncEngine(store=store, spotify=spotify, soundcloud=soundcloud, cfg=cfg)
    return engine, spotify, soundcloud


def standard(store, cfg):
    return make(
        store, cfg,
        spotify_lib=[MIDNIGHT_SP],
        soundcloud_lib=[ARCHANGEL_SC],
        spotify_catalog=[ARCHANGEL_SP],
        soundcloud_catalog=[MIDNIGHT_SC],
    )


def test_first_run_merges_both_libraries(store, sync_cfg):
    engine, spotify, soundcloud = standard(store, sync_cfg)
    report = engine.run()

    assert report.first_run is True
    assert report.aborted is None
    assert set(spotify.library) == {"sp1", "sp2"}
    assert set(soundcloud.library) == {"sc1", "sc2"}
    # A first run has no baseline, so removals are impossible by construction.
    assert all(a.action == LIKE for a in report.applied)
    assert store.counterpart(SPOTIFY, "sp1") == "sc1"
    assert store.counterpart(SOUNDCLOUD, "sc2") == "sp2"


def test_second_run_is_a_no_op(store, sync_cfg):
    engine, spotify, soundcloud = standard(store, sync_cfg)
    engine.run()

    engine2, spotify2, soundcloud2 = make(
        store, sync_cfg,
        spotify_lib=[MIDNIGHT_SP, ARCHANGEL_SP],
        soundcloud_lib=[ARCHANGEL_SC, MIDNIGHT_SC],
    )
    report = engine2.run()
    assert report.planned == []
    assert spotify2.like_calls == [] and soundcloud2.unlike_calls == []


def test_new_spotify_like_propagates_to_soundcloud(store, sync_cfg):
    engine, *_ = standard(store, sync_cfg)
    engine.run()

    new_sp = sp("sp9", "Teardrop", ["Massive Attack"], 330_000)
    new_sc = sc("sc9", "Massive Attack - Teardrop", ["Trip Hop Archive"], 331_000)
    engine2, spotify2, soundcloud2 = make(
        store, sync_cfg,
        spotify_lib=[MIDNIGHT_SP, ARCHANGEL_SP, new_sp],
        soundcloud_lib=[ARCHANGEL_SC, MIDNIGHT_SC],
        soundcloud_catalog=[new_sc],
    )
    report = engine2.run()

    assert [(a.provider, a.action, a.track_id) for a in report.applied] == [
        (SOUNDCLOUD, LIKE, "sc9")
    ]
    assert "sc9" in soundcloud2.library


def test_unlike_propagates_across(store, sync_cfg):
    engine, *_ = standard(store, sync_cfg)
    engine.run()

    # Drop Midnight City from Spotify; SoundCloud's copy should follow.
    engine2, spotify2, soundcloud2 = make(
        store, sync_cfg,
        spotify_lib=[ARCHANGEL_SP],
        soundcloud_lib=[ARCHANGEL_SC, MIDNIGHT_SC],
    )
    report = engine2.run()

    assert [(a.provider, a.action, a.track_id) for a in report.applied] == [
        (SOUNDCLOUD, UNLIKE, "sc1")
    ]
    assert "sc1" not in soundcloud2.library


def test_propagate_unlikes_off_keeps_removals_local(store, sync_cfg):
    engine, *_ = standard(store, sync_cfg)
    engine.run()

    sync_cfg.propagate_unlikes = False
    engine2, _, soundcloud2 = make(
        store, sync_cfg,
        spotify_lib=[ARCHANGEL_SP],
        soundcloud_lib=[ARCHANGEL_SC, MIDNIGHT_SC],
    )
    report = engine2.run()
    assert report.planned == []
    assert "sc1" in soundcloud2.library
    assert any("propagate_unlikes" in w for w in report.warnings)


def test_conflict_added_one_side_removed_other(store, sync_cfg):
    engine, *_ = standard(store, sync_cfg)
    engine.run()
    # Baseline: sp1<->sc1 and sp2<->sc2 all liked.
    store.set_mirror(SPOTIFY, ["sp1", "sp2"])
    store.set_mirror(SOUNDCLOUD, ["sc1", "sc2"])

    # Now: Spotify dropped sp1, SoundCloud *re-added* sc1 in the same window.
    # Model that as sc1 being absent from the previous SoundCloud mirror.
    store.set_mirror(SOUNDCLOUD, ["sc2"])
    engine2, spotify2, soundcloud2 = make(
        store, sync_cfg,
        spotify_lib=[ARCHANGEL_SP],
        soundcloud_lib=[ARCHANGEL_SC, MIDNIGHT_SC],
    )
    report = engine2.run()

    assert report.conflicts, "expected a reported conflict"
    # like_wins: the track comes back on Spotify rather than vanishing.
    assert ("spotify", LIKE, "sp1") in [
        (a.provider, a.action, a.track_id) for a in report.planned
    ]


def test_library_shrinkage_aborts_without_writing(store, sync_cfg):
    tracks_sp = [sp(f"sp{i}", f"Song {i}", ["Artist"], 200_000 + i) for i in range(20)]
    tracks_sc = [sc(f"sc{i}", f"Artist - Song {i}", ["Up"], 200_000 + i) for i in range(20)]
    engine, spotify, soundcloud = make(
        store, sync_cfg, spotify_lib=tracks_sp, soundcloud_lib=tracks_sc
    )
    engine.run()  # establish a baseline of 20 each

    engine2, spotify2, soundcloud2 = make(
        store, sync_cfg, spotify_lib=tracks_sp, soundcloud_lib=tracks_sc
    )
    # Simulate a truncated API read: only 3 of 20 come back.
    spotify2.truncate_to = 3
    report = engine2.run()

    assert report.aborted is not None
    assert "below the" in report.aborted
    assert soundcloud2.unlike_calls == []
    # The baseline must survive an aborted run, or the next one re-merges.
    assert len(store.mirror(SPOTIFY)) == 20


def test_force_shrink_overrides_the_rail(store, sync_cfg):
    tracks_sp = [sp(f"sp{i}", f"Song {i}", ["Artist"], 200_000 + i) for i in range(20)]
    engine, *_ = make(store, sync_cfg, spotify_lib=tracks_sp)
    engine.run()
    sync_cfg.force_shrink = True
    engine2, spotify2, _ = make(store, sync_cfg, spotify_lib=tracks_sp)
    spotify2.truncate_to = 3
    report = engine2.run()
    assert report.aborted is None


def baseline_pairs(store, n):
    """Link and baseline n pairs without going through the matcher."""
    sp_lib = [sp(f"sp{i}", f"Song {i}", ["Artist"], 200_000 + i * 1000) for i in range(n)]
    sc_lib = [sc(f"sc{i}", f"Artist - Song {i}", ["Up"], 200_000 + i * 1000)
              for i in range(n)]
    for i in range(n):
        store.put_link(f"sp{i}", f"sc{i}", score=1.0, method="manual", manual=True)
    store.cache_tracks(sp_lib + sc_lib)
    store.set_mirror(SPOTIFY, [t.id for t in sp_lib])
    store.set_mirror(SOUNDCLOUD, [t.id for t in sc_lib])
    return sp_lib, sc_lib


def test_mass_unlike_rail_drops_all_unlikes(store, sync_cfg):
    sp_lib, sc_lib = baseline_pairs(store, 24)
    sync_cfg.max_unlikes_per_run = 3

    # Ten tracks vanish from Spotify at once -- far more likely a fault than
    # ten deliberate unlikes, so none of it should be mirrored.
    engine, _, soundcloud = make(
        store, sync_cfg, spotify_lib=sp_lib[:14], soundcloud_lib=sc_lib
    )
    report = engine.run()

    assert soundcloud.unlike_calls == []
    assert any("max_unlikes_per_run" in w for w in report.warnings)
    assert len(report.deferred) == 10
    # Deferred work must be re-detected, so the baseline cannot have advanced.
    assert store.mirror(SPOTIFY) == {t.id for t in sp_lib}


def test_force_unlikes_applies_past_the_rail(store, sync_cfg):
    sp_lib, sc_lib = baseline_pairs(store, 24)
    sync_cfg.max_unlikes_per_run = 3
    sync_cfg.force_unlikes = True

    engine, _, soundcloud = make(
        store, sync_cfg, spotify_lib=sp_lib[:14], soundcloud_lib=sc_lib
    )
    report = engine.run()
    assert sum(1 for a in report.applied if a.action == UNLIKE) == 10
    assert set(soundcloud.library) == {f"sc{i}" for i in range(14)}


def test_deferred_unlikes_are_applied_on_a_later_run(store, sync_cfg):
    sp_lib, sc_lib = baseline_pairs(store, 24)
    sync_cfg.max_unlikes_per_run = 3
    engine, _, soundcloud = make(
        store, sync_cfg, spotify_lib=sp_lib[:14], soundcloud_lib=sc_lib
    )
    engine.run()

    # Raising the limit must let the held-back removals through next time.
    sync_cfg.max_unlikes_per_run = 50
    engine2, _, soundcloud2 = make(
        store, sync_cfg, spotify_lib=sp_lib[:14], soundcloud_lib=sc_lib
    )
    report = engine2.run()
    assert sum(1 for a in report.applied if a.action == UNLIKE) == 10
    assert set(soundcloud2.library) == {f"sc{i}" for i in range(14)}


def test_dry_run_writes_nothing_and_keeps_baseline(store, sync_cfg):
    engine, spotify, soundcloud = standard(store, sync_cfg)
    report = engine.run(dry_run=True)

    assert report.planned, "dry run should still produce a plan"
    assert report.applied == []
    assert spotify.like_calls == [] and soundcloud.like_calls == []
    assert store.mirror(SPOTIFY) == set(), "a dry run must not move the baseline"


def test_direction_to_spotify_ignores_spotify_side_removals(store, sync_cfg):
    sync_cfg.direction = "to-spotify"
    store.put_link("sp1", "sc1", score=1.0, manual=True)
    store.put_link("sp2", "sc2", score=1.0, manual=True)
    store.set_mirror(SPOTIFY, ["sp1", "sp2"])
    store.set_mirror(SOUNDCLOUD, ["sc1", "sc2"])

    # Removed on Spotify. Under to-spotify, SoundCloud is never written to.
    engine, _, soundcloud = make(
        store, sync_cfg,
        spotify_lib=[ARCHANGEL_SP],
        soundcloud_lib=[ARCHANGEL_SC, MIDNIGHT_SC],
    )
    report = engine.run()
    assert soundcloud.unlike_calls == []
    assert report.planned == []


def test_direction_to_spotify_still_applies_soundcloud_removals(tmp_path, sync_cfg):
    from likesync.state import Store

    store = Store(tmp_path / "other.sqlite3")
    try:
        sync_cfg.direction = "to-spotify"
        store.put_link("sp1", "sc1", score=1.0, manual=True)
        store.put_link("sp2", "sc2", score=1.0, manual=True)
        store.set_mirror(SPOTIFY, ["sp1", "sp2"])
        store.set_mirror(SOUNDCLOUD, ["sc1", "sc2"])

        engine, spotify, _ = make(
            store, sync_cfg,
            spotify_lib=[MIDNIGHT_SP, ARCHANGEL_SP],
            soundcloud_lib=[ARCHANGEL_SC],       # sc1 unliked
        )
        report = engine.run()
        assert [(a.provider, a.action, a.track_id) for a in report.applied] == [
            (SPOTIFY, UNLIKE, "sp1")
        ]
        assert "sp1" not in spotify.library
    finally:
        store.close()


def test_ignored_tracks_are_never_synced(store, sync_cfg):
    store.add_ignore(SPOTIFY, "sp1", "a 90 minute DJ set")
    engine, spotify, soundcloud = standard(store, sync_cfg)
    report = engine.run()
    assert "sc1" not in soundcloud.library
    assert all(a.track_id != "sp1" for a in report.applied)


def test_failed_write_is_retried_next_run(store, sync_cfg):
    engine, spotify, soundcloud = standard(store, sync_cfg)
    soundcloud.fail_ids = {"sc1"}
    report = engine.run()

    assert report.failed, "the failure should be reported"
    assert report.status == "partial"
    # sc1 never landed, so it must not be recorded as present.
    assert "sc1" not in store.mirror(SOUNDCLOUD)

    engine2, _, soundcloud2 = make(
        store, sync_cfg,
        spotify_lib=[MIDNIGHT_SP, ARCHANGEL_SP],
        soundcloud_lib=[ARCHANGEL_SC],
        soundcloud_catalog=[MIDNIGHT_SC],
    )
    report2 = engine2.run()
    assert ("soundcloud", LIKE, "sc1") in [
        (a.provider, a.action, a.track_id) for a in report2.applied
    ]


def test_unmatched_track_is_reported_and_not_retried_immediately(store, sync_cfg):
    lonely = sp("spX", "Totally Unreleased Dubplate", ["Nobody"], 180_000)
    engine, spotify, soundcloud = make(
        store, sync_cfg, spotify_lib=[lonely], soundcloud_catalog=[ARCHANGEL_SC]
    )
    report = engine.run()
    assert [u["track_id"] for u in report.unmatched] == ["spX"]

    searches_before = len(soundcloud.search_calls)
    engine2, _, soundcloud2 = make(
        store, sync_cfg, spotify_lib=[lonely], soundcloud_catalog=[ARCHANGEL_SC]
    )
    engine2.run()
    # Within the retry window it must not burn the search budget again.
    assert soundcloud2.search_calls == []
    assert searches_before > 0


def test_search_budget_is_respected(store, sync_cfg):
    sync_cfg.max_searches_per_run = 2
    lib = [sp(f"sp{i}", f"Obscure Thing {i}", ["Who"], 180_000 + i) for i in range(10)]
    engine, _, soundcloud = make(store, sync_cfg, spotify_lib=lib)
    report = engine.run()
    assert len(soundcloud.search_calls) <= 2
    assert report.searches <= 2


# --------------------------------------------------------------------------
# the inbox playlist
# --------------------------------------------------------------------------

from likesync.engine import INBOX_REASON, Inbox

INBOX_SC = sc("scI", "Four Tet - Baby", ["Text Records"], 283_000)
INBOX_SP = sp("spI", "Baby", ["Four Tet"], 282_000)


def with_inbox(store, cfg, tracks, *, spotify_lib=(), soundcloud_lib=(),
               spotify_catalog=(), soundcloud_catalog=(), fail=None):
    spotify = FakeProvider(SPOTIFY, spotify_lib, spotify_catalog)
    soundcloud = FakeProvider(SOUNDCLOUD, soundcloud_lib, soundcloud_catalog)
    # The inbox playlist's tracks must be likeable, so they live in the catalog.
    soundcloud.catalog.extend(tracks)

    def fetch():
        if fail:
            raise fail
        return list(tracks)

    engine = SyncEngine(
        store=store, spotify=spotify, soundcloud=soundcloud, cfg=cfg,
        inbox=Inbox(provider=SOUNDCLOUD, fetch=fetch),
    )
    return engine, spotify, soundcloud


def test_inbox_track_is_liked_and_propagated_in_one_run(store, sync_cfg):
    engine, spotify, soundcloud = with_inbox(
        store, sync_cfg, [INBOX_SC], spotify_catalog=[INBOX_SP]
    )
    report = engine.run()

    # Liked on SoundCloud, and pushed to Spotify in the same run.
    assert "scI" in soundcloud.library
    assert "spI" in spotify.library
    assert any(a.inbox and a.reason == INBOX_REASON for a in report.applied)
    assert report.inbox_taken == 1
    assert report.counts["inbox_new"] == 1


def test_inbox_track_already_liked_needs_no_action(store, sync_cfg):
    engine, _, soundcloud = with_inbox(
        store, sync_cfg, [INBOX_SC],
        soundcloud_lib=[INBOX_SC], spotify_catalog=[INBOX_SP],
    )
    report = engine.run()
    assert [a for a in report.applied if a.inbox] == []
    # Still recorded, so it is not reconsidered every run.
    assert "scI" in store.playlist_seen(SOUNDCLOUD)


def test_inbox_does_not_reconsider_a_track_it_has_taken_in(store, sync_cfg):
    engine, _, _ = with_inbox(
        store, sync_cfg, [INBOX_SC], spotify_catalog=[INBOX_SP]
    )
    engine.run()
    assert store.playlist_seen(SOUNDCLOUD) == {"scI"}

    # Second run, same playlist contents: nothing new to do.
    engine2, spotify2, soundcloud2 = with_inbox(
        store, sync_cfg, [INBOX_SC],
        spotify_lib=[INBOX_SP], soundcloud_lib=[INBOX_SC],
    )
    report = engine2.run()
    assert report.planned == []
    assert report.counts["inbox_new"] == 0


def test_unliking_an_inbox_track_is_not_undone_by_the_playlist(store, sync_cfg):
    """The reason the inbox is one-way.

    If playlist membership counted as part of the mirrored set, a playlist-only
    track could never be deleted: the removal would propagate, the playlist
    would still list it, and the next run would re-add it for ever.
    """
    engine, _, _ = with_inbox(
        store, sync_cfg, [INBOX_SC], spotify_catalog=[INBOX_SP]
    )
    engine.run()  # now liked on both sides

    # Unlike it on Spotify. The track is still sitting in the playlist.
    engine2, spotify2, soundcloud2 = with_inbox(
        store, sync_cfg, [INBOX_SC],
        spotify_lib=[], soundcloud_lib=[INBOX_SC],
    )
    report2 = engine2.run()
    assert [(a.provider, a.action) for a in report2.applied] == [
        (SOUNDCLOUD, UNLIKE)
    ]
    assert "scI" not in soundcloud2.library

    # Third run: it must stay gone, despite still being in the playlist.
    engine3, spotify3, soundcloud3 = with_inbox(
        store, sync_cfg, [INBOX_SC], spotify_lib=[], soundcloud_lib=[]
    )
    report3 = engine3.run()
    assert report3.planned == []
    assert soundcloud3.library == {}
    assert spotify3.library == {}


def test_failed_inbox_like_is_retried_next_run(store, sync_cfg):
    engine, _, soundcloud = with_inbox(
        store, sync_cfg, [INBOX_SC], spotify_catalog=[INBOX_SP]
    )
    soundcloud.fail_ids = {"scI"}
    report = engine.run()

    assert report.failed
    # Not recorded as taken in, so the next run tries again.
    assert store.playlist_seen(SOUNDCLOUD) == set()
    assert report.inbox_taken == 0

    engine2, _, soundcloud2 = with_inbox(
        store, sync_cfg, [INBOX_SC], spotify_catalog=[INBOX_SP]
    )
    report2 = engine2.run()
    assert "scI" in soundcloud2.library
    assert report2.inbox_taken == 1


def test_removing_a_track_from_the_playlist_changes_nothing(store, sync_cfg):
    engine, _, _ = with_inbox(
        store, sync_cfg, [INBOX_SC], spotify_catalog=[INBOX_SP]
    )
    engine.run()

    # Playlist emptied; the like it produced stays put.
    engine2, spotify2, soundcloud2 = with_inbox(
        store, sync_cfg, [],
        spotify_lib=[INBOX_SP], soundcloud_lib=[INBOX_SC],
    )
    report = engine2.run()
    assert report.planned == []
    assert "scI" in soundcloud2.library


def test_unreadable_playlist_does_not_stop_the_likes_sync(store, sync_cfg):
    from likesync.errors import ProviderError

    engine, spotify, soundcloud = with_inbox(
        store, sync_cfg, [INBOX_SC],
        spotify_lib=[MIDNIGHT_SP], soundcloud_catalog=[MIDNIGHT_SC],
        fail=ProviderError("playlist is private"),
    )
    report = engine.run()

    assert any("playlist is private" in w for w in report.warnings)
    # The ordinary likes sync still happened.
    assert "sc1" in soundcloud.library


def test_inbox_respects_ignores_and_the_length_filter(store, sync_cfg):
    long_mix = sc("scLong", "Someone - Two Hour Set", ["Radio"], 7_200_000)
    ignored = sc("scIgn", "Someone - Nope", ["Radio"], 200_000)
    store.add_ignore(SOUNDCLOUD, "scIgn", "not wanted")
    sync_cfg.skip_longer_than_ms = 900_000

    engine, _, soundcloud = with_inbox(
        store, sync_cfg, [INBOX_SC, long_mix, ignored],
        spotify_catalog=[INBOX_SP],
    )
    report = engine.run()
    assert report.counts["inbox_new"] == 1
    assert "scLong" not in soundcloud.library
    assert "scIgn" not in soundcloud.library


def test_dry_run_plans_the_inbox_without_taking_it_in(store, sync_cfg):
    engine, _, soundcloud = with_inbox(
        store, sync_cfg, [INBOX_SC], spotify_catalog=[INBOX_SP]
    )
    report = engine.run(dry_run=True)
    assert any(a.inbox for a in report.planned)
    assert soundcloud.like_calls == []
    assert store.playlist_seen(SOUNDCLOUD) == set()
