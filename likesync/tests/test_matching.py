"""Matching: the gates matter more than the scores."""

from __future__ import annotations

import pytest
from conftest import sc, sp

from likesync.matching import (
    duration_limits,
    norm_text,
    parse_title,
    score_pair,
    search_queries,
    similarity,
)

ACCEPT = 0.78
REVIEW = 0.62


def test_norm_text_folds_accents_punctuation_and_ampersands():
    assert norm_text("Björk & Sigur Rós") == "bjork and sigur ros"
    assert norm_text("Don't Stop Me Now!") == "dont stop me now"
    assert norm_text("Symphony No. 5, Pt. 2") == "symphony no 5 part 2"


def test_parse_title_strips_promo_noise():
    parts = parse_title("Nightcall (Official Video) [FREE DOWNLOAD] (HQ)")
    assert parts.base == "nightcall"
    assert parts.hard == frozenset()


def test_parse_title_keeps_meaningful_brackets():
    parts = parse_title("(I Can't Get No) Satisfaction")
    assert "satisfaction" in parts.base
    assert "cant get no" in parts.base


def test_parse_title_extracts_remix_and_remixer():
    parts = parse_title("Delilah (Skrillex Remix)")
    assert parts.base == "delilah"
    assert parts.hard == frozenset({"remix"})
    assert parts.remixers == ("skrillex",)


def test_parse_title_extracts_featured_artists():
    parts = parse_title("Praise You (feat. Camille Yarbrough)")
    assert parts.base == "praise you"
    assert "camille yarbrough" in parts.featured


def test_parse_title_splits_artist_for_soundcloud():
    parts = parse_title("Burial - Archangel", split_leading_artist=True)
    assert parts.leading_artist == "burial"
    assert parts.base == "archangel"


def test_parse_title_drops_remaster_and_original_mix():
    assert parse_title("Blue Monday - 2016 Remaster").base == "blue monday"
    assert parse_title("Strobe (Original Mix)").base == "strobe"
    assert parse_title("Strobe (Original Mix)").hard == frozenset()


def test_similarity_is_order_insensitive():
    assert similarity("archangel burial", "burial archangel") > 0.95


def test_similarity_damps_short_subset_titles():
    # "love" should not be a perfect match for "love story in the rain".
    assert similarity("love", "love story in the rain") < 0.9


# --- the gates -------------------------------------------------------------


def test_identical_isrc_is_an_instant_match():
    a = sc("1", "Whatever - Mislabelled", duration_ms=1_000)
    b = sp("2", "Completely Different Title", ["Nobody"], 400_000)
    a = a.__class__(**{**a.__dict__, "isrc": "GBAYE0601498"})
    b = b.__class__(**{**b.__dict__, "isrc": "gbaye0601498"})
    score = score_pair(a, b)
    assert score.value == 1.0
    assert score.method == "isrc"
    assert not score.blocked


def test_remix_never_matches_the_original():
    remix = sc("1", "Fred again.. - Delilah (Skrillex Remix)", duration_ms=215_000)
    original = sp("2", "Delilah (pull me out of this)", ["Fred again.."], 214_000)
    score = score_pair(remix, original)
    assert score.blocked
    assert "version mismatch" in score.reasons[0]


def test_different_remixers_never_match():
    a = sc("1", "Artist - Song (Skrillex Remix)", duration_ms=200_000)
    b = sp("2", "Song - Noisia Remix", ["Artist"], 200_000)
    score = score_pair(a, b)
    assert score.blocked
    assert "different remixer" in score.reasons[0]


def test_live_never_matches_studio():
    live = sc("1", "Radiohead - Creep (Live at Glastonbury)", duration_ms=240_000)
    studio = sp("2", "Creep", ["Radiohead"], 238_000)
    assert score_pair(live, studio).blocked


def test_acoustic_and_instrumental_are_distinct_recordings():
    for marker in ("Acoustic", "Instrumental"):
        a = sc("1", f"Artist - Song ({marker})", duration_ms=200_000)
        b = sp("2", "Song", ["Artist"], 200_000)
        assert score_pair(a, b).blocked, marker


def test_wildly_different_durations_are_rejected():
    # A 60-minute DJ set that happens to be named after a track.
    mix = sc("1", "Artist - Song", duration_ms=3_600_000)
    track = sp("2", "Song", ["Artist"], 200_000)
    assert score_pair(mix, track).blocked


def test_same_title_different_artist_is_rejected():
    a = sc("1", "Nirvana - Something In The Way", duration_ms=230_000)
    b = sp("2", "Something In The Way", ["Totally Other Band"], 230_000)
    score = score_pair(a, b)
    assert score.blocked or score.value < REVIEW


# --- real-world shapes that should match ----------------------------------


@pytest.mark.parametrize("sc_title,sc_user,sp_title,sp_artists,sc_ms,sp_ms", [
    ("Burial - Archangel", "Hyperdub", "Archangel", ["Burial"], 237_000, 236_400),
    ("M83 - Midnight City (Official Video)", "Indie Blog", "Midnight City",
     ["M83"], 244_000, 243_000),
    ("Fred again.. - Delilah (Skrillex Remix)", "Promo",
     "Delilah (pull me out of this) - Skrillex Remix",
     ["Fred again..", "Skrillex"], 215_000, 213_500),
    ("Daft Punk - Around The World [HQ] [FREE DOWNLOAD]", "Filter House",
     "Around the World", ["Daft Punk"], 429_000, 427_000),
    ("Bicep - Glue", "Ninja Tune", "Glue", ["Bicep"], 271_000, 269_000),
    ("Floating Points - Last Bloom (Original Mix)", "Ninja Tune",
     "Last Bloom", ["Floating Points"], 390_000, 388_000),
    ("Four Tet - Baby feat. Ellie Goulding", "Text Records",
     "Baby (feat. Ellie Goulding)", ["Four Tet"], 283_000, 282_000),
])
def test_real_world_pairs_match(sc_title, sc_user, sp_title, sp_artists, sc_ms, sp_ms):
    a = sc("1", sc_title, [sc_user], duration_ms=sc_ms)
    b = sp("2", sp_title, sp_artists, duration_ms=sp_ms)
    score = score_pair(a, b)
    assert not score.blocked, score.reasons
    assert score.value >= ACCEPT, f"{score.value:.3f} {score.reasons}"


def test_matching_is_symmetric():
    a = sc("1", "Burial - Archangel", ["Hyperdub"], duration_ms=237_000)
    b = sp("2", "Archangel", ["Burial"], duration_ms=236_400)
    assert score_pair(a, b).value == pytest.approx(score_pair(b, a).value, abs=0.02)


def test_duration_limits_scale_with_track_length():
    assert duration_limits(200_000, 200_000) == 15_000
    assert duration_limits(600_000, 600_000) == 36_000
    assert duration_limits(3_600_000, 3_600_000) == 45_000   # capped


def test_search_queries_are_ordered_most_precise_first():
    track = sc("1", "Burial - Archangel", ["Hyperdub"], duration_ms=237_000)
    queries = search_queries(track, target="spotify")
    assert queries
    assert queries[0].startswith('track:"archangel"')
    assert 'artist:"burial"' in queries[0]
    assert len(queries) <= 4


def test_search_queries_for_soundcloud_are_plain_text():
    track = sp("1", "Archangel", ["Burial"], duration_ms=236_000)
    queries = search_queries(track, target="soundcloud")
    assert queries[0] == "burial archangel"
    assert all("track:" not in q for q in queries)
