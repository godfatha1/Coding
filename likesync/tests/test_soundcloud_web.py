"""The web provider, run in real Chromium against pages modelled on the live site.

The fixtures were written from the real pages (see fixtures_sc.py), and the
JavaScript under test runs in an actual browser, so this covers the selectors,
the structural fallback and click-and-verify rather than only the Python
around them. The same provider code was also run against live soundcloud.com;
these tests pin down what that run established.
"""

from __future__ import annotations

import fixtures_sc as fx
import pytest

from likesync.errors import AuthError, ProviderError
from likesync.providers.soundcloud_web import (
    ID_PREFIX,
    SoundCloudWebProvider,
    data_track,
    parse_duration,
    row_to_track,
    track_url,
    tracks_in,
    web_track_id,
)
from likesync.websession import BrowserPage, launch_chromium


def _chromium_available() -> bool:
    """Can a browser actually be launched here, the way production does it?"""
    try:
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError:
        return False
    pw = sync_playwright().start()
    try:
        launch_chromium(pw, headless=True).close()
        return True
    except Exception:  # noqa: BLE001 - any failure means "skip the browser tests"
        return False
    finally:
        pw.stop()


needs_browser = pytest.mark.skipif(
    not _chromium_available(), reason="no launchable Chromium"
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
# a page that serves canned markup and canned page data
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    b = launch_chromium(pw, headless=True)
    yield b
    b.close()
    pw.stop()


class HtmlPage(BrowserPage):
    """A BrowserPage that serves fixtures instead of the live site.

    Subclasses the real class, so the extraction JavaScript runs through
    exactly the code path production uses. ``data`` gives, per URL, the batches
    of track lists the page "loads": the first on arrival, one more per scroll.
    """

    def __init__(self, page, routes: dict[str, str], data: dict | None = None):
        super().__init__(page, min_interval_s=0.0)
        self.routes = routes
        self.data = data or {}
        self.visited: list[str] = []
        self.scrolls = 0
        self.growing = False          # a page that never stops getting taller
        self.redirect_to: str | None = None
        self._url = "about:blank"
        self._pending: list[list] = []
        self._seen: list = []

    def goto(self, url: str) -> None:
        self.visited.append(url)
        self._url = self.redirect_to or url
        html = next((h for key, h in self.routes.items() if key in url), fx.EMPTY_PAGE)
        self._page.set_content(html)
        batches = next((b for key, b in self.data.items() if key in url), [])
        self._pending = [list(batch) for batch in batches]
        self._seen = list(self._pending.pop(0)) if self._pending else []

    def current_url(self) -> str:
        return self._url

    def wait(self, ms: int) -> None:
        self._page.wait_for_timeout(min(ms, 20))

    def start_capture(self) -> None:
        self._seen = []

    def captured(self):
        return list(self._seen)

    def scroll_step(self, settle_ms: int = 450) -> int:
        self.scrolls += 1
        if self._pending:
            self._seen.extend(self._pending.pop(0))
        return 1000 + (self.scrolls if self.growing else 0)

    def scroll_to_end(self, *, max_scrolls=400, settle_ms=450) -> bool:
        return not self.growing


def make(browser, routes, data=None, **kw):
    page = HtmlPage(browser.new_page(), routes, data)
    kw.setdefault("settle_ms", 1000)   # four still rounds, and they are instant
    kw.setdefault("max_scrolls", 40)
    return SoundCloudWebProvider(page, write_pause_s=0, **kw), page


def ids(tracks):
    return [t.id.removeprefix(ID_PREFIX) for t in tracks]


LIKES = "/you/likes"


# --------------------------------------------------------------------------
# reading likes from the data the page loads
# --------------------------------------------------------------------------


def test_data_track_keeps_everything_the_matcher_wants():
    track = data_track(fx.ARCHANGEL)
    assert track.id == f"{ID_PREFIX}burial/archangel"
    assert track.url == "https://soundcloud.com/burial/archangel"
    assert track.duration_ms == 237_000          # full length, not the preview
    assert track.isrc == "GBAYE0601498"
    assert track.artists[0] == "Burial"          # the real artist, not the label
    # The site's own stable id rides along for rename detection.
    assert track.extra["urn"] == "soundcloud:tracks:101"
    assert track.extra["artist_source"] == "publisher"


def test_data_track_needs_a_track_address():
    assert data_track({**fx.ARCHANGEL, "permalink_url": None}) is None
    assert data_track({**fx.ARCHANGEL,
                       "permalink_url": "https://soundcloud.com/burial/sets/untrue"}) is None


def test_tracks_in_finds_tracks_at_any_depth():
    payload = {"collection": [fx.like(fx.ARCHANGEL), fx.liked_playlist(fx.QUITS)]}
    assert sorted(t["id"] for t in tracks_in(payload)) == [101, 201]


@needs_browser
def test_likes_are_read_from_the_batches_the_page_loads(browser):
    provider, page = make(browser, {LIKES: fx.LIKES_PAGE}, {LIKES: [
        [fx.likes_batch(fx.like(fx.ARCHANGEL), more=True)],
        [fx.likes_batch(fx.like(fx.DELILAH), more=True)],
        [fx.likes_batch(fx.like(fx.PODCAST), more=False)],
    ]})
    tracks = provider.liked()

    assert ids(tracks) == [
        "burial/archangel", "fredagain/delilah-skrillex-remix",
        "unsound/unsound-podcast-109",
    ]
    assert provider.source == "page data"
    assert all(t.duration_ms for t in tracks)
    # The 89-minute podcast is recognisable as such, which rows never allow.
    assert tracks[2].duration_ms > 5_000_000
    # Stopped as soon as the page said the list had ended.
    assert page.scrolls == 2


@needs_browser
def test_tracks_inside_a_liked_playlist_are_not_likes(browser):
    provider, _ = make(browser, {LIKES: fx.LIKES_PAGE}, {LIKES: [[
        fx.likes_batch(fx.like(fx.ARCHANGEL), fx.liked_playlist(fx.QUITS, fx.AMBER),
                       fx.like(fx.DELILAH), more=False),
    ]]})
    assert ids(provider.liked()) == [
        "burial/archangel", "fredagain/delilah-skrillex-remix"
    ]


@needs_browser
def test_likes_that_never_finish_loading_are_an_error_not_a_short_list(browser):
    """A short list would read to the engine as a mass unlike."""
    provider, page = make(browser, {LIKES: fx.LIKES_PAGE}, {LIKES: [
        [fx.likes_batch(fx.like(fx.ARCHANGEL), more=True)],
        [fx.likes_batch(fx.like(fx.DELILAH), more=True)],
        # ...and then the page promises more but never delivers it.
    ]})
    with pytest.raises(ProviderError, match="incomplete"):
        provider.liked()
    # Waited well past the point at which mere stillness would have passed.
    assert page.scrolls >= 12


@needs_browser
def test_an_empty_library_is_an_empty_list(browser):
    provider, _ = make(browser, {}, {LIKES: [[fx.likes_batch(more=False)]]})
    assert provider.liked() == []
    assert provider.source == "page data"


@needs_browser
def test_a_sidebar_list_does_not_pass_for_the_main_one(browser):
    provider, _ = make(browser, {LIKES: fx.LIKES_PAGE}, {LIKES: [
        [fx.likes_batch(fx.like(fx.QUITS), more=True, path="/users/7/likes"),
         fx.likes_batch(fx.like(fx.ARCHANGEL), fx.like(fx.DELILAH), more=True)],
        [fx.likes_batch(fx.like(fx.PODCAST), more=False)],
    ]})
    assert ids(provider.liked()) == [
        "burial/archangel", "fredagain/delilah-skrillex-remix",
        "unsound/unsound-podcast-109",
    ]


@needs_browser
def test_duplicate_entries_are_collapsed(browser):
    provider, _ = make(browser, {}, {LIKES: [[
        fx.likes_batch(fx.like(fx.ARCHANGEL), fx.like(fx.ARCHANGEL), more=False),
    ]]})
    assert ids(provider.liked()) == ["burial/archangel"]


# --------------------------------------------------------------------------
# reading likes from the visible rows, when no data is seen
# --------------------------------------------------------------------------


@needs_browser
def test_rows_are_the_fallback_and_carry_no_duration(browser):
    provider, _ = make(browser, {LIKES: fx.LIKES_PAGE})
    tracks = provider.liked()

    assert provider.source == "page markup"
    assert ids(tracks) == [
        "burial/archangel", "fredagain/delilah-skrillex-remix",
        "unsound/unsound-podcast-109",
    ]
    first = tracks[0]
    assert first.title == "Archangel"
    assert first.artists == ("Hyperdub",)
    assert first.url == "https://soundcloud.com/burial/archangel"
    assert first.extra["artist_source"] == "uploader"
    # The live site shows no duration in a row, so neither does the fixture.
    assert all(t.duration_ms is None for t in tracks)


@needs_browser
def test_rows_skip_a_liked_playlist_and_the_tracks_listed_inside_it(browser):
    """The reader that preceded this one counted those tracks as likes."""
    provider, _ = make(browser, {LIKES: fx.LIKES_PAGE})
    found = ids(provider.liked())
    assert "flume/quits" not in found and "flume/amber" not in found
    assert not any("sets" in f for f in found)
    assert len(found) == 3


@needs_browser
def test_structural_fallback_when_class_names_are_gone(browser):
    """If SoundCloud renames every class, the two-link structure still reads."""
    provider, _ = make(browser, {LIKES: fx.GENERIC_PAGE})
    tracks = provider.liked()
    assert [t.title for t in tracks] == ["Last Bloom", "Glue", "Midnight City"]
    assert tracks[0].artists == ("Ninja Tune",)
    assert tracks[2].duration_ms == 244_000


@needs_browser
def test_rows_are_filled_in_from_whatever_data_the_page_did_load(browser):
    # No likes list was seen, but row details were loaded as they scrolled in.
    provider, _ = make(browser, {LIKES: fx.LIKES_PAGE},
                       {LIKES: [[("/tracks", [fx.ARCHANGEL])]]})
    tracks = provider.liked()
    assert provider.source == "page markup"
    assert tracks[0].duration_ms == 237_000 and tracks[0].isrc == "GBAYE0601498"
    assert tracks[1].duration_ms is None


@needs_browser
def test_a_rows_page_that_keeps_growing_is_an_error(browser):
    provider, page = make(browser, {LIKES: fx.LIKES_PAGE}, max_scrolls=8)
    page.growing = True
    with pytest.raises(ProviderError, match="incomplete"):
        provider.liked()


@needs_browser
def test_unreadable_rows_are_reported_rather_than_silently_empty(browser):
    html = """<ul><li class="soundList__item"><a href="/you/library">Library</a></li>
              <li class="soundList__item"><a href="/discover/y">y</a></li></ul>"""
    provider, _ = make(browser, {LIKES: html})
    with pytest.raises(ProviderError, match="markup has probably changed"):
        provider.liked()


@needs_browser
def test_falls_back_to_the_users_own_profile_likes_page(browser):
    """The library page yields nothing; the public likes page is a simpler layout."""
    provider, page = make(
        browser,
        {},
        {LIKES: [[fx.me("my-name")]],
         "/my-name/likes": [[fx.likes_batch(fx.like(fx.ARCHANGEL), more=False)]]},
    )
    assert ids(provider.liked()) == ["burial/archangel"]
    assert page.visited == [
        "https://soundcloud.com/you/likes", "https://soundcloud.com/my-name/likes"
    ]


@needs_browser
def test_a_configured_likes_page_is_used_directly(browser):
    provider, page = make(
        browser, {},
        {"/my-name/likes": [[fx.likes_batch(fx.like(fx.DELILAH), more=False)]]},
        likes_url="https://soundcloud.com/my-name/likes",
    )
    assert ids(provider.liked()) == ["fredagain/delilah-skrillex-remix"]
    assert page.visited == ["https://soundcloud.com/my-name/likes"]


@needs_browser
def test_nothing_readable_anywhere_says_what_to_set(browser):
    provider, _ = make(browser, {})
    with pytest.raises(ProviderError, match="soundcloud.likes_url"):
        provider.liked()


@needs_browser
def test_signed_out_redirect_is_reported_as_an_auth_error(browser):
    provider, page = make(browser, {})
    page.redirect_to = "https://soundcloud.com/signin?redirect_url=/you/likes"
    with pytest.raises(AuthError, match="login soundcloud --web"):
        provider.liked()


# --------------------------------------------------------------------------
# search and the inbox playlist
# --------------------------------------------------------------------------


@needs_browser
def test_search_uses_the_results_the_page_loads(browser):
    provider, page = make(
        browser, {"/search/sounds": fx.SEARCH_PAGE},
        {"/search/sounds": [[fx.search_batch(fx.ARCHANGEL, fx.DELILAH, fx.PODCAST)]]},
    )
    results = provider.search("burial archangel", limit=2)
    assert ids(results) == ["burial/archangel", "fredagain/delilah-skrillex-remix"]
    assert results[0].duration_ms == 237_000
    assert "q=burial%20archangel" in page.visited[-1]


@needs_browser
def test_search_falls_back_to_the_visible_results(browser):
    provider, _ = make(browser, {"/search/sounds": fx.SEARCH_PAGE})
    results = provider.search("burial archangel", limit=2)
    assert ids(results) == ["burial-uk-1/archangel", "trashpixels/burial-archangel"]
    assert results[0].artists == ("Burial Uk",)


@needs_browser
def test_playlist_inbox_waits_for_every_member_to_load(browser):
    members = [fx.ARCHANGEL, fx.DELILAH, fx.QUITS, fx.AMBER]
    provider, page = make(
        browser,
        {"/sets/sync-me": fx.playlist_page(members, hydrated=2)},
        {"/sets/sync-me": [[], [("/tracks", [fx.QUITS])], [("/tracks", [fx.AMBER])]]},
    )
    tracks = provider.playlist_tracks("https://soundcloud.com/me/sets/sync-me")
    assert ids(tracks) == [
        "burial/archangel", "fredagain/delilah-skrillex-remix",
        "flume/quits", "flume/amber",
    ]
    assert all(t.duration_ms for t in tracks)
    assert provider.source == "page data"
    assert page.scrolls == 2


@needs_browser
def test_playlist_members_that_never_load_are_an_error(browser):
    members = [fx.ARCHANGEL, fx.DELILAH, fx.QUITS]
    provider, _ = make(
        browser, {"/sets/sync-me": fx.playlist_page(members, hydrated=1)},
    )
    with pytest.raises(ProviderError, match="2 of 3 playlist tracks never loaded"):
        provider.playlist_tracks("https://soundcloud.com/me/sets/sync-me")


@needs_browser
def test_playlist_falls_back_to_its_visible_rows(browser):
    page_html = fx.playlist_page([fx.ARCHANGEL, fx.QUITS], hydrated=2).replace(
        "window.__sc_hydration", "window.__something_else"
    )
    provider, _ = make(browser, {"/sets/sync-me": page_html})
    tracks = provider.playlist_tracks("me/sets/sync-me")
    assert ids(tracks) == ["burial/archangel", "flume/quits"]
    assert tracks[0].artists == ("Hyperdub",)
    assert provider.source == "page markup"


# --------------------------------------------------------------------------
# writing: the like buttons really toggle in these fixtures
# --------------------------------------------------------------------------

ARCHANGEL_ID = f"{ID_PREFIX}burial/archangel"


def labels(page):
    return page.extract(fx.LIKED_BUTTONS)


@needs_browser
def test_like_clicks_the_tracks_own_button_and_no_other(browser):
    """A track page also has like buttons for related tracks in the sidebar."""
    provider, page = make(browser, {"burial/archangel": fx.track_page(liked=False)})
    assert provider.like([ARCHANGEL_ID]) == [ARCHANGEL_ID]
    assert provider.failures == {}
    assert labels(page) == ["Unlike", "Like", "Like"]


@needs_browser
def test_the_right_button_is_found_wherever_it_sits_in_the_page(browser):
    provider, page = make(
        browser, {"burial/archangel": fx.track_page(liked=False, sidebar_first=True)}
    )
    assert provider.like([ARCHANGEL_ID]) == [ARCHANGEL_ID]
    # Sidebar buttons come first in the document here, and are still untouched.
    assert labels(page) == ["Like", "Like", "Unlike"]


@needs_browser
def test_liking_an_already_liked_track_is_a_no_op(browser):
    provider, page = make(browser, {"burial/archangel": fx.track_page(liked=True)})
    clicked: list[str] = []
    page.click = lambda sel, **kw: clicked.append(sel) or True
    assert provider.like([ARCHANGEL_ID]) == [ARCHANGEL_ID]
    assert clicked == [], "must not click when it is already in the wanted state"


@needs_browser
def test_unlike_clicks_and_verifies(browser):
    provider, page = make(browser, {"burial/archangel": fx.track_page(liked=True)})
    assert provider.unlike([ARCHANGEL_ID]) == [ARCHANGEL_ID]
    assert labels(page) == ["Like", "Like", "Like"]


@needs_browser
def test_unliking_a_track_that_is_already_gone_is_a_no_op(browser):
    provider, _ = make(browser, {"burial/archangel": fx.track_page(liked=False)})
    assert provider.unlike([ARCHANGEL_ID]) == [ARCHANGEL_ID]


@needs_browser
def test_without_its_usual_scope_the_button_is_still_found_if_unambiguous(browser):
    provider, page = make(
        browser, {"burial/archangel": fx.track_page(liked=False, scoped=False)}
    )
    assert provider.like([ARCHANGEL_ID]) == [ARCHANGEL_ID]
    assert labels(page) == ["Unlike", "Like", "Like"]


@needs_browser
def test_an_ambiguous_page_is_refused_rather_than_guessed_at(browser):
    provider, page = make(browser, {"burial/archangel": fx.AMBIGUOUS_TRACK_PAGE})
    assert provider.like([ARCHANGEL_ID]) == []
    assert "could not tell which like button" in provider.failures[ARCHANGEL_ID]
    assert labels(page) == ["Like", "Like"], "nothing may be clicked on a guess"


@needs_browser
def test_missing_like_button_is_a_per_track_failure_not_a_run_failure(browser):
    glue = f"{ID_PREFIX}bicep/glue"
    provider, _ = make(
        browser,
        {
            "burial/archangel": fx.track_page(liked=False, own_button=False),
            "bicep/glue": fx.track_page(liked=False),
        },
    )
    assert provider.like([ARCHANGEL_ID, glue]) == [glue]
    assert "no like button" in provider.failures[ARCHANGEL_ID]


@needs_browser
def test_a_track_whose_address_leads_elsewhere_is_never_clicked(browser):
    """A dead address can redirect to a page with like buttons of its own."""
    provider, page = make(browser, {"burial/archangel": fx.track_page(liked=False)})
    page.redirect_to = "https://soundcloud.com/burial"
    assert provider.like([ARCHANGEL_ID]) == []
    assert "now leads to /burial" in provider.failures[ARCHANGEL_ID]
    assert labels(page) == ["Like", "Like", "Like"]


@needs_browser
def test_a_click_that_does_not_take_is_reported(browser):
    provider, page = make(browser, {"burial/archangel": fx.track_page(liked=False)})
    page.click = lambda sel, **kw: True  # pretend to click, change nothing
    assert provider.like([ARCHANGEL_ID]) == []
    assert "still reads" in provider.failures[ARCHANGEL_ID]


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------


@needs_browser
def test_probe_reports_a_usable_session(browser):
    provider, _ = make(
        browser,
        {LIKES: fx.LIKES_PAGE, "/search/sounds": fx.SEARCH_PAGE},
        {LIKES: [[fx.likes_batch(fx.like(fx.ARCHANGEL), fx.like(fx.DELILAH), more=True)]],
         "/search/sounds": [[fx.search_batch(fx.ARCHANGEL)]]},
    )
    report = provider.probe()
    assert report["verdict"] == "ready"
    by_name = {c["check"]: c for c in report["checks"]}
    assert by_name["signed in"]["ok"] is True
    assert by_name["likes: data the page loads"]["tracks_so_far"] == 2
    assert by_name["likes: data the page loads"]["with_duration"] == 2
    assert by_name["likes: visible rows (fallback)"]["tracks_parsed"] == 3
    assert by_name["search"]["via_data"] is True


@needs_browser
def test_probe_says_when_only_the_rows_can_be_read(browser):
    provider, _ = make(
        browser, {LIKES: fx.LIKES_PAGE, "/search/sounds": fx.SEARCH_PAGE}
    )
    assert provider.probe()["verdict"].startswith("ready (visible rows only")


@needs_browser
def test_probe_says_when_not_signed_in(browser):
    provider, page = make(browser, {})
    page.redirect_to = "https://soundcloud.com/signin"
    assert "not signed in" in provider.probe()["verdict"]


@needs_browser
def test_probe_says_when_nothing_can_be_read(browser):
    provider, _ = make(browser, {})
    assert "could not be read" in provider.probe()["verdict"]


# --------------------------------------------------------------------------
# launching a browser
# --------------------------------------------------------------------------


def test_a_visible_window_on_a_screenless_machine_fails_fast_and_clearly(monkeypatch):
    """The sign-in window cannot open on a server; say so, not a launch log."""
    from likesync import websession
    from likesync.errors import ConfigError

    monkeypatch.setattr(websession.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    class NeverLaunched:
        class chromium:
            @staticmethod
            def launch(**kwargs):
                raise AssertionError("should not try to launch without a screen")

    with pytest.raises(ConfigError, match="no screen") as caught:
        websession.launch_chromium(NeverLaunched(), headless=False)
    assert "session export" in str(caught.value)
    assert "\n" not in str(caught.value)


def test_launch_failure_is_reported_in_one_line(monkeypatch):
    from likesync import websession
    from likesync.errors import ConfigError

    monkeypatch.setattr(websession, "discover_browsers", list)

    class Broken:
        class chromium:
            @staticmethod
            def launch(**kwargs):
                raise RuntimeError("Executable doesn't exist at /x/chrome\nCall log:\n"
                                   + "  - noise\n" * 50)

    with pytest.raises(ConfigError) as caught:
        websession.launch_chromium(Broken(), headless=True)
    message = str(caught.value)
    assert "Executable doesn't exist at /x/chrome" in message
    assert "Call log" not in message and "noise" not in message


def test_launch_falls_back_to_a_browser_already_on_the_machine(monkeypatch):
    from likesync import websession

    monkeypatch.setattr(websession, "discover_browsers", lambda: ["/usr/bin/some-chrome"])
    tried: list[str | None] = []

    class OnlySystemBrowser:
        class chromium:
            @staticmethod
            def launch(**kwargs):
                tried.append(kwargs.get("executable_path"))
                if "executable_path" not in kwargs:
                    raise RuntimeError("managed build not downloaded")
                return "browser"

    assert websession.launch_chromium(OnlySystemBrowser(), headless=True) == "browser"
    # Playwright's own build first, then what the machine already has.
    assert tried == [None, "/usr/bin/some-chrome"]
