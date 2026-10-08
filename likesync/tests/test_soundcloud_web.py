"""The web provider, exercised against real markup in a real browser.

soundcloud.com is not reachable from CI, so the DOM extractor is validated
against representative markup rendered in actual Chromium. That proves the
JavaScript works -- selectors, structural fallback, the like-button toggle --
rather than only that the Python around it does.
"""

from __future__ import annotations

import fixtures_sc as fx
import pytest

from likesync.errors import AuthError, ProviderError
from likesync.providers.soundcloud_web import (
    ID_PREFIX,
    SoundCloudWebProvider,
    parse_duration,
    row_to_track,
    track_url,
    web_track_id,
)
from likesync.websession import BrowserPage, find_browser

CHROME = find_browser()
needs_browser = pytest.mark.skipif(
    CHROME is None, reason="no Chromium binary available"
)


# --------------------------------------------------------------------------
# pure functions
# --------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("3:57", 237_000),
    ("0:30", 30_000),
    ("1:32:10", 5_530_000),
    ("12:05", 725_000),
    ("", None),
    ("nonsense", None),
])
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


def test_ids_round_trip_to_permalinks():
    tid = web_track_id("/burial/archangel")
    assert tid == f"{ID_PREFIX}burial/archangel"
    assert track_url(tid) == "https://soundcloud.com/burial/archangel"


def test_api_mode_ids_are_rejected_with_a_pointer_to_the_docs():
    with pytest.raises(ProviderError, match="re-linking"):
        track_url("soundcloud:tracks:12345")


@pytest.mark.parametrize("path", [
    "burial/sets", "burial/likes", "you/likes", "discover/x", "burial",
    "artist/tracks", "artist/reposts",
])
def test_non_track_paths_are_rejected(path):
    assert row_to_track({"path": path, "title": "Something"}) is None


def test_row_without_a_title_is_rejected():
    assert row_to_track({"path": "burial/archangel", "title": "  "}) is None


# --------------------------------------------------------------------------
# DOM extraction, in a real browser
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    b = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
    yield b
    b.close()
    pw.stop()


class HtmlPage(BrowserPage):
    """A BrowserPage serving canned HTML instead of a live site.

    Subclasses the real class so the extraction JS runs through exactly the
    code path production uses.
    """

    def __init__(self, page, routes: dict[str, str]):
        super().__init__(page, min_interval_s=0.0)
        self.routes = routes
        self.visited: list[str] = []
        self._url = "about:blank"

    def goto(self, url: str) -> None:
        self.visited.append(url)
        self._url = url
        for pattern, html in self.routes.items():
            if pattern in url:
                self._page.set_content(html)
                return
        self._page.set_content(fx.EMPTY_PAGE)

    def current_url(self) -> str:
        return self._url

    def wait(self, ms: int) -> None:
        self._page.wait_for_timeout(min(ms, 50))

    def scroll_to_end(self, *, max_scrolls=400, settle_ms=450) -> bool:
        return True


def make(browser, routes, **kw):
    page = HtmlPage(browser.new_page(), routes)
    return SoundCloudWebProvider(page, write_pause_s=0, **kw), page


@needs_browser
def test_reads_likes_with_soundcloud_class_names(browser):
    provider, _ = make(browser, {"/you/likes": fx.LIKES_PAGE})
    tracks = provider.liked()

    assert [t.id for t in tracks] == [
        f"{ID_PREFIX}burial/archangel",
        f"{ID_PREFIX}fredagain/delilah-skrillex-remix",
        f"{ID_PREFIX}someonelse/a-ninety-minute-set",
    ]
    first = tracks[0]
    assert first.title == "Archangel"
    assert first.artists == ("Hyperdub",)
    assert first.duration_ms == 237_000
    assert first.url == "https://soundcloud.com/burial/archangel"
    # The uploader is whoever posted it, so matching prefers the parsed artist.
    assert first.extra["artist_source"] == "uploader"
    # The /burial/sets row is a profile page, not a track.
    assert all("sets" not in t.id for t in tracks)


@needs_browser
def test_long_duration_is_read_for_dj_sets(browser):
    provider, _ = make(browser, {"/you/likes": fx.LIKES_PAGE})
    mix = next(t for t in provider.liked() if "ninety" in t.id)
    assert mix.duration_ms == 5_530_000  # 1:32:10 -- skip_longer_than_ms sees this


@needs_browser
def test_structural_fallback_when_class_names_are_gone(browser):
    """If SoundCloud renames every class, the two-link structure still reads."""
    provider, _ = make(browser, {"/you/likes": fx.GENERIC_PAGE})
    tracks = provider.liked()
    assert [t.title for t in tracks] == ["Last Bloom", "Glue", "Midnight City"]
    assert tracks[0].artists == ("Ninja Tune",)
    assert tracks[2].duration_ms == 244_000


@needs_browser
def test_empty_likes_page_is_not_an_error(browser):
    provider, _ = make(browser, {"/you/likes": fx.EMPTY_PAGE})
    assert provider.liked() == []


@needs_browser
def test_signed_out_redirect_is_reported_as_an_auth_error(browser):
    provider, page = make(browser, {})

    original = page.goto

    def to_signin(url: str) -> None:
        original(url)
        page._url = "https://soundcloud.com/signin?redirect=/you/likes"

    page.goto = to_signin
    with pytest.raises(AuthError, match="login soundcloud --web"):
        provider.liked()


@needs_browser
def test_incomplete_scroll_refuses_to_return_a_partial_library(browser):
    """A truncated likes list would read to the engine as a mass unlike."""
    provider, page = make(browser, {"/you/likes": fx.LIKES_PAGE})
    page.scroll_to_end = lambda **kw: False
    with pytest.raises(ProviderError, match="incomplete"):
        provider.liked()


@needs_browser
def test_unreadable_rows_are_reported_rather_than_silently_empty(browser):
    html = """<ul class="soundList">
      <li class="soundList__item"><a class="soundTitle__title" href="/you/likes">x</a></li>
      <li class="soundList__item"><a class="soundTitle__title" href="/discover/y">y</a></li>
    </ul>"""
    provider, _ = make(browser, {"/you/likes": html})
    with pytest.raises(ProviderError, match="markup has probably changed"):
        provider.liked()


@needs_browser
def test_search_reads_results_and_respects_the_limit(browser):
    provider, page = make(browser, {"/search/sounds": fx.GENERIC_PAGE})
    results = provider.search("ninja tune", limit=2)
    assert [t.title for t in results] == ["Last Bloom", "Glue"]
    assert "q=ninja%20tune" in page.visited[-1]


@needs_browser
def test_playlist_inbox_is_read_from_its_url(browser):
    provider, page = make(browser, {"/sets/sync-me": fx.GENERIC_PAGE})
    tracks = provider.playlist_tracks("https://soundcloud.com/me/sets/sync-me")
    assert len(tracks) == 3
    assert page.visited[-1].endswith("/sets/sync-me")


# --------------------------------------------------------------------------
# writing: the like button really toggles in these fixtures
# --------------------------------------------------------------------------


@needs_browser
def test_like_clicks_and_verifies_the_new_state(browser):
    provider, _ = make(
        browser, {"burial/archangel": fx.track_page(liked=False)}
    )
    assert provider.like([f"{ID_PREFIX}burial/archangel"]) == [
        f"{ID_PREFIX}burial/archangel"
    ]
    assert provider.failures == {}


@needs_browser
def test_liking_an_already_liked_track_is_a_no_op(browser):
    provider, page = make(
        browser, {"burial/archangel": fx.track_page(liked=True)}
    )
    clicked: list[str] = []
    page.click = lambda sel, **kw: clicked.append(sel) or True
    assert provider.like([f"{ID_PREFIX}burial/archangel"]) == [
        f"{ID_PREFIX}burial/archangel"
    ]
    assert clicked == [], "must not click when it is already in the wanted state"


@needs_browser
def test_unlike_clicks_and_verifies(browser):
    provider, _ = make(
        browser, {"burial/archangel": fx.track_page(liked=True)}
    )
    assert provider.unlike([f"{ID_PREFIX}burial/archangel"]) == [
        f"{ID_PREFIX}burial/archangel"
    ]


@needs_browser
def test_unliking_a_track_that_is_already_gone_is_a_no_op(browser):
    provider, _ = make(
        browser, {"burial/archangel": fx.track_page(liked=False)}
    )
    assert provider.unlike([f"{ID_PREFIX}burial/archangel"]) == [
        f"{ID_PREFIX}burial/archangel"
    ]


@needs_browser
def test_missing_like_button_is_a_per_track_failure_not_a_run_failure(browser):
    provider, _ = make(
        browser,
        {
            "burial/archangel": fx.track_page(liked=False, readable=False),
            "bicep/glue": fx.track_page(liked=False),
        },
    )
    done = provider.like([f"{ID_PREFIX}burial/archangel", f"{ID_PREFIX}bicep/glue"])
    assert done == [f"{ID_PREFIX}bicep/glue"]
    assert "no like button" in provider.failures[f"{ID_PREFIX}burial/archangel"]


@needs_browser
def test_a_click_that_does_not_take_is_reported(browser):
    provider, page = make(
        browser, {"burial/archangel": fx.track_page(liked=False)}
    )
    page.click = lambda sel, **kw: True  # pretend to click, change nothing
    assert provider.like([f"{ID_PREFIX}burial/archangel"]) == []
    assert "still reads" in provider.failures[f"{ID_PREFIX}burial/archangel"]


@needs_browser
def test_probe_reports_a_usable_session(browser):
    provider, _ = make(
        browser,
        {"/you/likes": fx.LIKES_PAGE, "/search/sounds": fx.GENERIC_PAGE},
    )
    report = provider.probe()
    assert report["verdict"] == "ready"
    assert report["checks"][0]["ok"] is True
    likes_check = next(c for c in report["checks"] if "likes page" in c["check"])
    assert likes_check["tracks_parsed"] == 3


@needs_browser
def test_probe_says_when_not_signed_in(browser):
    provider, page = make(browser, {})
    original = page.goto

    def to_signin(url: str) -> None:
        original(url)
        page._url = "https://soundcloud.com/signin"

    page.goto = to_signin
    report = provider.probe()
    assert "not signed in" in report["verdict"]
