"""SoundCloud through its own web pages, with no API credentials.

SoundCloud has not granted new API applications in years, so this provider
does what you would do by hand: open your likes page and scroll it, open a
track page and click the like button.

How it reads, in order of preference:

1. **The track lists the page loads for itself.** As you scroll, the site
   fetches your likes in batches; those batches carry everything the matcher
   wants (duration, ISRC, the real artist) and say outright when the list has
   ended. likesync reads them as they arrive. It never sends a request of its
   own, and it reads no credentials -- see ``websession.BrowserPage``.
2. **The visible rows**, when that data is not seen. Rows show no duration at
   all (the site draws it on the waveform), so this is a fallback, not an
   equal: it works, with weaker matching.

Both were checked against the live site, which is how two faults in a
markup-only reader came to light: the rows include liked *playlists*, whose
member tracks are easily mistaken for likes, and "the page stopped growing"
is a poor test for "the list has ended".

Automated access is contrary to SoundCloud's terms of service. It is your own
account and library, at a pace far below human browsing -- but that is your
call to make, and the README says so plainly.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import time
import urllib.parse
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from ..errors import AuthError, ProviderError
from ..models import SOUNDCLOUD, Track
from ..websession import HOME, Page
from .base import DictMemo, Memo
from .soundcloud import parse_track

log = logging.getLogger("likesync.soundcloud-web")

ID_PREFIX = "soundcloud:permalink:"
_DURATION_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
_LIKES_PATH = re.compile(r"(?:^|/)(?:track_)?likes$")
SEARCH_PATH = "/search/tracks"
TRACKS_PATH = "/tracks"

# Reserved first path segments: site pages, not usernames.
_NOT_A_TRACK = frozenset({
    "you", "discover", "search", "stream", "feed", "upload", "settings",
    "pages", "tags", "charts", "people", "notifications", "messages",
    "terms", "pro", "imprint", "jobs", "mobile", "signin", "login",
})
# Reserved second segments: profile sub-pages are two-segment paths too, so
# /artist/likes and /artist/sets must not be mistaken for /artist/track-slug.
_NOT_A_SLUG = frozenset({
    "likes", "tracks", "reposts", "sets", "albums", "comments", "followers",
    "following", "popular-tracks", "insights", "stats", "recent", "spotlight",
    "toptracks", "playlists", "groups",
})

LIST_CONTAINERS = (
    ".soundList__item", ".searchList__item", ".trackList__item",
    "[class*=soundList__item]", "li",
)
PLAYLIST_CONTAINERS = (
    ".trackList__item", ".systemPlaylistTrackList__item",
    "[class*=trackList__item]", "li",
)

# One row per track from a listing page. Uses the site's class names first,
# then structure: a two-segment link (the track) and a one-segment link (the
# uploader) inside the same row. __CONTAINERS__ is filled in per page type.
_EXTRACT_ROWS_TEMPLATE = r"""
() => {
  // Read the href attribute directly rather than resolving it against
  // location.origin: hrefs may be relative or absolute, and depending on the
  // document's own origin makes this needlessly brittle.
  const pathOf = (el) => {
    let h = (el && el.getAttribute('href')) || '';
    if (!h) return null;
    h = h.replace(/^[a-z][a-z0-9+.-]*:\/\/[^/]+/i, '');
    h = h.split('?')[0].split('#')[0].replace(/\/+$/, '');
    return h.startsWith('/') ? h : null;
  };
  const segs = (el) => {
    const p = pathOf(el);
    return p ? p.split('/').filter(Boolean) : null;
  };
  const twoSeg = (el) => {
    const s = segs(el);
    return s && s.length === 2 ? '/' + s.join('/') : null;
  };
  const oneSeg = (el) => {
    const s = segs(el);
    return s && s.length === 1 ? s[0] : null;
  };
  const text = (el) => (el ? (el.textContent || '').trim().replace(/\s+/g, ' ') : '');

  let items = [];
  for (const sel of __CONTAINERS__) {
    items = Array.from(document.querySelectorAll(sel));
    if (items.length > 0) break;
  }

  const seen = new Set();
  const rows = [];
  for (const item of items) {
    // A liked playlist or album sits in the same list as liked tracks, and
    // lists its own tracks inside the row. None of those are likes.
    if (item.querySelector('.sound.playlist, [class*=compactTrackList]')) continue;

    let titleEl = item.querySelector('a.soundTitle__title, a.trackItem__trackTitle')
      || item.querySelector('[class*=soundTitle__title], [class*=trackTitle]');
    if (titleEl) {
      // A title that is not a track address (a set, a station) is not a track.
      if (!twoSeg(titleEl)) continue;
    } else {
      // No recognisable title element: fall back to the row's structure.
      titleEl = Array.from(item.querySelectorAll('a[href]')).find(a => twoSeg(a));
      if (!titleEl) continue;
    }
    const path = twoSeg(titleEl);
    if (seen.has(path)) continue;

    let userEl = item.querySelector('a.soundTitle__username, a.trackItem__username')
      || item.querySelector('[class*=soundTitle__username], [class*=trackItem__username]');
    if (!userEl || !oneSeg(userEl)) {
      userEl = Array.from(item.querySelectorAll('a[href]')).find(a => oneSeg(a));
    }

    // Rows on the live site carry no duration; this only helps other layouts.
    let duration = '';
    const exact = /^\d{1,2}:\d{2}(?::\d{2})?$/;
    for (const el of item.querySelectorAll('span, div, time')) {
      const t = (el.textContent || '').trim();
      if (exact.test(t)) { duration = t; break; }
    }

    seen.add(path);
    rows.push({ path, title: text(titleEl), user: text(userEl), duration });
  }
  return rows;
}
"""


def extract_rows_js(containers: Sequence[str] = LIST_CONTAINERS) -> str:
    return _EXTRACT_ROWS_TEMPLATE.replace("__CONTAINERS__", json.dumps(list(containers)))


# The like button of the track this page is about -- and only that one. A
# track page also carries like buttons for related tracks in the sidebar, so
# an unscoped match risks liking somebody else's track. If the target is not
# unambiguous, nothing is marked and nothing gets clicked.
_LIKE_STATE = r"""
() => {
  document.querySelectorAll('[data-likesync-target]')
    .forEach(e => e.removeAttribute('data-likesync-target'));
  const all = Array.from(document.querySelectorAll('button.sc-button-like'));
  let how = 'listenContext';
  let candidates = all.filter(b => b.closest('.listenContext'));
  if (!candidates.length) {
    how = 'unscoped';
    const elsewhere = '.soundBadge, .soundBadgeList, .sidebarModule, .soundList__item, '
      + '.searchList__item, .trackList__item, [class*=compactTrackList], '
      + '[class*=sidebar], [class*=related]';
    candidates = all.filter(b => !b.closest(elsewhere));
  }
  if (candidates.length !== 1) {
    return { found: false, ambiguous: candidates.length > 1, buttons: all.length };
  }
  const b = candidates[0];
  const pressed = b.getAttribute('aria-pressed');
  const label = (b.getAttribute('aria-label') || b.getAttribute('title')
    || b.textContent || '').trim();
  let liked = null;
  if (pressed === 'true') liked = true;
  else if (pressed === 'false') liked = false;
  else if (/sc-button-selected/.test(b.className || '')) liked = true;
  else if (/^(unlike|liked)\b/i.test(label)) liked = true;
  else if (/^like\b/i.test(label)) liked = false;
  b.setAttribute('data-likesync-target', '1');
  return { found: true, selector: '[data-likesync-target="1"]', liked, label, how,
           buttons: all.length };
}
"""

# The playlist a page was rendered from: every member's id, with full details
# for the first few and bare ids for the rest.
_PLAYLIST_DATA = r"""
() => {
  const entry = (window.__sc_hydration || []).find(h => h && h.hydratable === 'playlist');
  const tracks = entry && entry.data && Array.isArray(entry.data.tracks)
    ? entry.data.tracks : null;
  return tracks;
}
"""


def web_track_id(path: str) -> str:
    """Stable id for a track, derived from its permalink path."""
    return ID_PREFIX + str(path).strip("/")


def track_url(track_id: str) -> str:
    """Permalink for a web-mode track id."""
    tid = str(track_id)
    if tid.startswith(ID_PREFIX):
        return f"{HOME}/{tid[len(ID_PREFIX):]}"
    if tid.startswith(("http://", "https://")):
        return tid
    raise ProviderError(
        f"soundcloud-web cannot address {tid!r}. Web mode identifies tracks by "
        "permalink; a state file written in API mode needs re-linking "
        "(see 'Switching modes' in the README)."
    )


def parse_duration(text: str) -> int | None:
    """"3:42" or "1:03:42" -> milliseconds."""
    m = _DURATION_RE.match((text or "").strip())
    if not m:
        return None
    hours, minutes, seconds = m.group(1), m.group(2), m.group(3)
    total = int(minutes) * 60 + int(seconds)
    if hours:
        total += int(hours) * 3600
    return total * 1000


def _track_path(value: Any) -> str | None:
    """"owner/slug" from a permalink URL or path, or None if it is not a track."""
    if not value:
        return None
    path = urllib.parse.urlparse(str(value)).path.strip("/")
    owner, _, slug = path.partition("/")
    if not owner or not slug or "/" in slug:
        return None
    if owner.lower() in _NOT_A_TRACK or slug.lower() in _NOT_A_SLUG:
        return None
    return path


def row_to_track(row: dict) -> Track | None:
    """A Track from one visible row. No duration, no ISRC: the page shows neither."""
    path = _track_path(row.get("path"))
    title = str(row.get("title") or "").strip()
    if not path or not title:
        return None
    user = str(row.get("user") or "").strip()
    return Track(
        provider=SOUNDCLOUD,
        id=web_track_id(path),
        title=title,
        artists=(user,) if user else (),
        duration_ms=parse_duration(str(row.get("duration") or "")),
        isrc=None,
        url=f"{HOME}/{path}",
        raw_title=title,
        # The uploader here is whoever posted it, so prefer the artist parsed
        # out of "Artist - Title" when scoring.
        extra={"artist_source": "uploader", "permalink": path, "source": "markup"},
    )


def data_track(raw: Any) -> Track | None:
    """A Track from the data a page loaded, addressed by permalink.

    The permalink is the id because it is what a write needs (the track page's
    address) and what the visible rows also provide, so both ways of reading
    agree. The site's own stable id rides along as ``extra["urn"]``; the engine
    uses it to notice when an uploader renames a track's address.
    """
    base = parse_track(raw)
    if base is None:
        return None
    path = _track_path(raw.get("permalink_url"))
    if not path:
        return None
    return dataclasses.replace(
        base,
        id=web_track_id(path),
        url=f"{HOME}/{path}",
        extra={**base.extra, "permalink": path, "urn": base.id, "source": "data"},
    )


def tracks_in(payload: Any) -> Iterator[dict]:
    """Every track object anywhere inside a payload."""
    if isinstance(payload, dict):
        if payload.get("kind") == "track" and payload.get("permalink_url"):
            yield payload
        for value in payload.values():
            if isinstance(value, (dict, list)):
                yield from tracks_in(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from tracks_in(value)


def _unique(tracks: Sequence[Track]) -> list[Track]:
    seen: dict[str, Track] = {}
    for track in tracks:
        seen.setdefault(track.id, track)
    return list(seen.values())


class SoundCloudWebProvider:
    name = SOUNDCLOUD

    def __init__(
        self,
        page: Page,
        *,
        memo: Memo | None = None,
        likes_url: str = "",
        max_scrolls: int = 400,
        settle_ms: int = 450,
        write_pause_s: float = 1.5,
        verify_writes: bool = True,
    ) -> None:
        self.page = page
        self.memo = memo or DictMemo()
        self.likes_url = likes_url.strip()
        self.max_scrolls = max_scrolls
        self.settle_ms = max(int(settle_ms), 1)
        self.write_pause_s = write_pause_s
        self.verify_writes = verify_writes
        self.failures: dict[str, str] = {}
        # How the last list was read: "page data" or "page markup".
        self.source = ""

    # -- shared reading machinery -----------------------------------------

    def _open(self, url: str) -> None:
        self.page.start_capture()
        self.page.goto(url)
        self._guard_signed_out()
        self.page.wait(1500)

    def _guard_signed_out(self) -> None:
        url = self.page.current_url()
        if "/signin" in url or "/login" in url:
            raise AuthError(
                "SoundCloud redirected to the sign-in page, so the saved "
                "browser session has expired.\n"
                "Re-run: likesync login soundcloud --web"
            )

    def _scroll_until(self, status: Callable[[], bool | None]) -> bool:
        """Scroll until the list is fully loaded.

        ``status`` reports what the page's own data says: True when it has
        announced the end of the list, False when it has promised more, None
        when it has said nothing. Returns whether the list can be taken as
        complete.

        With no word from the page the only evidence is that it stopped
        growing, so that takes about three seconds of stillness. When the page
        has promised more, stillness is not an ending: it is waited out far
        longer, and then reported as a failure rather than a short list.
        """
        still_needed = max(4, -(-3000 // self.settle_ms))
        stall_limit = max(12, -(-12000 // self.settle_ms))
        quiet = 0
        last: tuple[int, int] | None = None
        for _ in range(self.max_scrolls):
            state = status()
            if state is True:
                return True
            marker = (self.page.scroll_step(self.settle_ms), len(self.page.captured()))
            quiet = quiet + 1 if marker == last else 0
            last = marker
            if state is None and quiet >= still_needed:
                return True
            if state is False and quiet >= stall_limit:
                return False
        return status() is True

    def _rows(self, containers: Sequence[str] = LIST_CONTAINERS) -> tuple[int, list[Track]]:
        rows = self.page.extract(extract_rows_js(containers)) or []
        return len(rows), _unique([t for t in (row_to_track(r) for r in rows) if t])

    def _details(self, extra: Any = None) -> dict[str, Track]:
        """Everything the page has loaded about any track, by track id."""
        found: dict[str, Track] = {}
        payloads: list[Any] = [payload for _, payload in self.page.captured()]
        if extra is not None:
            payloads.append(extra)
        for payload in payloads:
            for raw in tracks_in(payload):
                track = data_track(raw)
                if track and track.title:
                    found.setdefault(track.id, track)
        return found

    def _fill_in(self, tracks: list[Track], extra: Any = None) -> list[Track]:
        """Swap row-only tracks for their full details wherever the page has them."""
        details = self._details(extra)
        return [details.get(track.id, track) for track in tracks]

    # -- likes --------------------------------------------------------------

    def _likes_from_data(self) -> tuple[list[Track], bool | None]:
        """(liked tracks, complete?) from the batches the page loaded.

        ``complete`` is None when no batch was seen at all. Only an entry's own
        ``track`` counts: a liked playlist's entry holds a ``playlist``, and the
        tracks inside it are not likes.
        """
        by_path: dict[str, list[Track]] = {}
        ended: dict[str, bool] = {}
        for path, payload in self.page.captured():
            if not _LIKES_PATH.search(path) or not isinstance(payload, dict):
                continue
            collection = payload.get("collection")
            if not isinstance(collection, list):
                continue
            bucket = by_path.setdefault(path, [])
            for entry in collection:
                raw = entry.get("track") if isinstance(entry, dict) else None
                track = data_track(raw) if isinstance(raw, dict) else None
                if track:
                    bucket.append(track)
            if not payload.get("next_href"):
                ended[path] = True
        if not by_path:
            return [], None
        # A page can load a few likes for a sidebar as well; the list being
        # scrolled is the one with the most in it.
        main = max(by_path, key=lambda p: len(by_path[p]))
        return _unique(by_path[main]), ended.get(main, False)

    def _profile_likes_url(self) -> str | None:
        for path, payload in self.page.captured():
            if path == "/me" and isinstance(payload, dict):
                link = str(payload.get("permalink_url") or "").rstrip("/")
                if link.startswith("https://soundcloud.com/"):
                    return f"{link}/likes"
        return None

    def _read_likes(self, url: str) -> list[Track] | None:
        """Read one likes page. None means nothing readable was found there."""
        self._open(url)
        settled = self._scroll_until(lambda: self._likes_from_data()[1])
        tracks, complete = self._likes_from_data()

        if complete is not None:
            if not complete:
                # A short list would read to the engine as a mass unlike.
                raise ProviderError(
                    f"soundcloud-web: your likes were still loading after "
                    f"{len(tracks)} tracks when the page stopped responding "
                    f"(or the {self.max_scrolls}-scroll limit was reached), so "
                    "the list is incomplete. Nothing was changed. Try again, "
                    "or raise soundcloud.max_scrolls for a very large library."
                )
            self.source = "page data"
            log.info("soundcloud-web: read %d liked track(s) from page data", len(tracks))
            return tracks

        found, tracks = self._rows()
        if found and not tracks:
            raise ProviderError(
                f"soundcloud-web: found {found} rows on the likes page but could "
                "not read a single track from them. The site's markup has "
                "probably changed; run `likesync probe soundcloud`."
            )
        if not tracks:
            return None
        if not settled:
            raise ProviderError(
                f"soundcloud-web: reached the {self.max_scrolls}-scroll limit "
                "while the likes page was still growing, so the list is "
                "incomplete. Raise soundcloud.max_scrolls and re-run."
            )
        self.source = "page markup"
        log.warning(
            "soundcloud-web: read %d liked track(s) from the visible rows only; "
            "durations are unavailable this run, so matching is weaker. "
            "`likesync probe soundcloud` shows why.", len(tracks),
        )
        return self._fill_in(tracks)

    def liked(self) -> list[Track]:
        """Every liked track. Raises rather than returning a partial list."""
        first = self.likes_url or f"{HOME}/you/likes"
        tracks = self._read_likes(first)
        if tracks is not None:
            return tracks
        # The library page showed nothing readable. A public profile's likes
        # page is a different and simpler layout, so try the user's own.
        fallback = None if self.likes_url else self._profile_likes_url()
        if fallback:
            log.info("soundcloud-web: trying %s instead", fallback)
            tracks = self._read_likes(fallback)
            if tracks is not None:
                return tracks
        raise ProviderError(
            "soundcloud-web: could not read any likes from "
            f"{first}{' or ' + fallback if fallback else ''}. If you really have "
            "none, like one track first. Otherwise set soundcloud.likes_url to "
            "your public likes page (https://soundcloud.com/<your-name>/likes) "
            "and run `likesync probe soundcloud`."
        )

    # -- search ---------------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> list[Track]:
        self._open(f"{HOME}/search/sounds?q={urllib.parse.quote(query)}")
        # Results are ranked, so the first batch is all that is useful.
        for _ in range(8):
            if any(path == SEARCH_PATH for path, _ in self.page.captured()):
                break
            self.page.wait(400)
        results: list[Track] = []
        for path, payload in self.page.captured():
            if path != SEARCH_PATH or not isinstance(payload, dict):
                continue
            for raw in payload.get("collection") or []:
                track = data_track(raw)
                if track:
                    results.append(track)
        if results:
            return _unique(results)[:limit]
        _, tracks = self._rows()
        return self._fill_in(tracks)[:limit]

    def search_isrc(self, isrc: str, limit: int = 5) -> list[Track]:
        # SoundCloud has no ISRC search; ISRC only ever confirms a pair when
        # both sides already publish one.
        return []

    # -- playlists (the inbox) ---------------------------------------------

    def playlist_tracks(self, url: str) -> list[Track]:
        if not url.startswith(("http://", "https://")):
            url = f"{HOME}/{url.lstrip('/')}"
        self._open(url)

        members = self.page.extract(_PLAYLIST_DATA)
        if isinstance(members, list) and members:
            ids = [str(m.get("id")) for m in members if isinstance(m, dict) and m.get("id")]
            known: dict[str, dict] = {}

            def gather() -> None:
                for raw in tracks_in(members):
                    known.setdefault(str(raw.get("id")), raw)
                for path, payload in self.page.captured():
                    if path == TRACKS_PATH:
                        for raw in tracks_in(payload):
                            known.setdefault(str(raw.get("id")), raw)

            def all_loaded() -> bool:
                gather()
                return all(i in known for i in ids)

            # The page says how many members there are, so "more to come" is
            # known for certain until every one has its details.
            if not self._scroll_until(all_loaded):
                missing = sum(1 for i in ids if i not in known)
                raise ProviderError(
                    f"soundcloud-web: {missing} of {len(ids)} playlist tracks "
                    "never loaded, so the inbox is incomplete. Try again."
                )
            self.source = "page data"
            tracks = [t for t in (data_track(known[i]) for i in ids) if t]
            log.info("soundcloud-web: read %d track(s) from the inbox playlist", len(tracks))
            return _unique(tracks)

        settled = self.page.scroll_to_end(
            max_scrolls=self.max_scrolls, settle_ms=self.settle_ms
        )
        found, tracks = self._rows(PLAYLIST_CONTAINERS)
        if found and not tracks:
            raise ProviderError(
                f"soundcloud-web: found {found} rows in the playlist but could "
                "not read a track from them; run `likesync probe soundcloud`."
            )
        if tracks and not settled:
            raise ProviderError(
                "soundcloud-web: the playlist was still growing at the scroll "
                "limit, so the inbox is incomplete. Raise soundcloud.max_scrolls."
            )
        self.source = "page markup"
        return self._fill_in(tracks)

    # -- writing ------------------------------------------------------------------

    def like(self, track_ids: Sequence[str]) -> list[str]:
        return self._write_many(track_ids, want_liked=True)

    def unlike(self, track_ids: Sequence[str]) -> list[str]:
        return self._write_many(track_ids, want_liked=False)

    def _write_many(
        self, track_ids: Sequence[str], *, want_liked: bool
    ) -> list[str]:
        done: list[str] = []
        self.failures = {}
        for index, track_id in enumerate(track_ids):
            if index and self.write_pause_s:
                time.sleep(self.write_pause_s)
            try:
                if self._set_like(track_id, want_liked=want_liked):
                    done.append(track_id)
            except AuthError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad track is not fatal
                log.warning(
                    "soundcloud-web: %s failed for %s: %s",
                    "like" if want_liked else "unlike", track_id, exc,
                )
                self.failures[track_id] = str(exc)
        return done

    def _like_button(self) -> dict:
        state = self.page.extract(_LIKE_STATE) or {}
        if state.get("found"):
            return state
        if state.get("ambiguous"):
            raise ProviderError(
                "could not tell which like button belongs to this track "
                f"({state.get('buttons')} on the page), so nothing was clicked"
            )
        raise ProviderError(
            "no like button for this track (deleted, private or "
            "region-blocked, or the page layout changed)"
        )

    def _set_like(self, track_id: str, *, want_liked: bool) -> bool:
        """Put one track into the wanted state. Idempotent."""
        url = track_url(track_id)
        self.page.goto(url)
        self._guard_signed_out()
        self.page.wait(1200)

        # A dead address can redirect to some other page that has like buttons
        # of its own. Only ever click on the page that was asked for.
        wanted = urllib.parse.urlparse(url).path.strip("/").lower()
        landed = urllib.parse.urlparse(self.page.current_url()).path.strip("/").lower()
        if landed != wanted:
            raise ProviderError(
                f"the track's address now leads to /{landed or ''} "
                "(moved, deleted or made private)"
            )

        state = self._like_button()
        if state.get("liked") is want_liked:
            return True  # already where we want it

        if not self.page.click(state["selector"]):
            raise ProviderError("could not click the like button")
        self.page.wait(1200)

        if not self.verify_writes:
            return True
        after = self._like_button()
        if after.get("liked") is want_liked:
            return True
        if after.get("liked") is None:
            # Button state unreadable: the click landed, so take it, but say so.
            log.info(
                "soundcloud-web: could not confirm %s for %s; assuming it took",
                "like" if want_liked else "unlike", track_id,
            )
            return True
        raise ProviderError(
            f"clicked like but the button still reads "
            f"{'liked' if after.get('liked') else 'not liked'}"
        )

    # -- diagnostics -----------------------------------------------------------------

    def probe(self) -> dict[str, Any]:
        """Read-only check of the session and of both ways of reading a page."""
        report: dict[str, Any] = {"checks": []}

        def record(name: str, **fields: Any) -> None:
            report["checks"].append({"check": name, **fields})

        first = self.likes_url or f"{HOME}/you/likes"
        self.page.start_capture()
        self.page.goto(first)
        self.page.wait(2500)
        url = self.page.current_url()
        signed_in = "/signin" not in url and "/login" not in url
        record("signed in", ok=signed_in, landed_on=url)
        if not signed_in:
            report["verdict"] = "not signed in - run: likesync login soundcloud --web"
            return report

        # A couple of scrolls is enough to see whether each way of reading works.
        for _ in range(2):
            self.page.scroll_step(self.settle_ms)
        data_tracks, complete = self._likes_from_data()
        found, row_tracks = self._rows()
        profile = self._profile_likes_url()
        record(
            "likes: data the page loads",
            ok=complete is not None,
            tracks_so_far=len(data_tracks),
            with_duration=sum(1 for t in data_tracks if t.duration_ms),
            with_isrc=sum(1 for t in data_tracks if t.isrc),
            sample=[t.display() for t in data_tracks[:3]],
        )
        record(
            "likes: visible rows (fallback)",
            ok=bool(row_tracks), rows_found=found, tracks_parsed=len(row_tracks),
            sample=[t.display() for t in row_tracks[:3]],
        )
        record("own profile likes page (second fallback)", url=profile or "not seen")
        likes_ok = complete is not None or bool(row_tracks) or bool(profile)

        self._open(f"{HOME}/search/sounds?q=burial%20archangel")
        self.page.wait(1500)
        via_data = [p for p, _ in self.page.captured() if p == SEARCH_PATH]
        _, search_rows = self._rows()
        record("search", ok=bool(via_data or search_rows),
               via_data=bool(via_data), rows_parsed=len(search_rows))

        if likes_ok and (via_data or search_rows):
            report["verdict"] = (
                "ready" if complete is not None or profile
                else "ready (visible rows only: no durations, weaker matching)"
            )
        else:
            report["verdict"] = (
                "signed in, but the page could not be read - send this report"
            )
        report["note"] = (
            "Nothing was changed: liking a track is never probed. Use "
            "`likesync plan` to see what a real run would do."
        )
        return report
