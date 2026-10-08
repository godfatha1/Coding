"""SoundCloud driven through its own web pages, with no API credentials.

SoundCloud has not granted new API applications in years. This provider does
what you would do by hand: open your likes page, read what is on it, and click
the like button on a track page. It reads the rendered DOM and clicks controls
-- it does not call internal endpoints and handles no credentials of any kind.

Two consequences worth knowing:

* Automated access is contrary to SoundCloud's terms of service. It is your own
  account and your own library, the request rate is far below human browsing,
  and nothing here touches anyone else's data -- but it is your call to make.
* The page markup is not a published interface. Selectors are therefore written
  as layered candidates with a structural fallback, and ``likesync probe
  soundcloud`` reports what the extractor can actually see so a change on their
  side is diagnosable in one command.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from collections.abc import Sequence
from typing import Any

from ..errors import AuthError, ProviderError
from ..models import SOUNDCLOUD, Track
from ..websession import HOME, Page
from .base import DictMemo, Memo

log = logging.getLogger("likesync.soundcloud-web")

ID_PREFIX = "soundcloud:permalink:"
_DURATION_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")

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

# Extracts one row per track from a listing page. Tries SoundCloud's own class
# names first, then falls back to structure: a two-segment link (the track) and
# a one-segment link (the uploader) inside the same list item.
_EXTRACT_ROWS = r"""
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
  const containers = [
    '.soundList__item', '.trackList__item', '.searchList__item',
    '.systemPlaylistTrackList__item', '[class*=soundList__item]', 'li',
  ];
  let items = [];
  for (const sel of containers) {
    items = Array.from(document.querySelectorAll(sel));
    if (items.length > 0) break;
  }
  const seen = new Set();
  const rows = [];
  for (const item of items) {
    let titleEl =
      item.querySelector('a.soundTitle__title') ||
      item.querySelector('[class*=soundTitle__title]');
    if (!titleEl || !twoSeg(titleEl)) {
      titleEl = Array.from(item.querySelectorAll('a[href]')).find(a => twoSeg(a));
    }
    if (!titleEl) continue;
    const path = twoSeg(titleEl);
    if (!path || seen.has(path)) continue;

    let userEl =
      item.querySelector('a.soundTitle__username') ||
      item.querySelector('[class*=soundTitle__username]');
    if (!userEl || !oneSeg(userEl)) {
      userEl = Array.from(item.querySelectorAll('a[href]')).find(a => oneSeg(a));
    }

    let duration = '';
    const durEl =
      item.querySelector('.sound__duration .sc-visuallyhidden') ||
      item.querySelector('[class*=duration] .sc-visuallyhidden') ||
      item.querySelector('.sound__duration');
    if (durEl) duration = (durEl.textContent || '').trim();
    if (!duration) {
      // Look for an element whose own text *is* a duration. Scanning the
      // item's whole textContent instead would miss "...M834:04", where the
      // artist name runs straight into the time with no separator.
      const exact = /^\d{1,2}:\d{2}(?::\d{2})?$/;
      for (const el of item.querySelectorAll('*')) {
        const t = (el.textContent || '').trim();
        if (exact.test(t)) { duration = t; break; }
      }
    }
    if (!duration) {
      const m = (item.textContent || '').match(/\s(\d{1,2}:\d{2}(?::\d{2})?)\s/);
      if (m) duration = m[1];
    }

    seen.add(path);
    rows.push({
      path,
      title: (titleEl.textContent || '').trim().replace(/\s+/g, ' '),
      user: userEl ? (userEl.textContent || '').trim().replace(/\s+/g, ' ') : '',
      duration,
    });
  }
  return rows;
}
"""

# Reads the like button's state on a track page.
_LIKE_STATE = r"""
() => {
  const selectors = [
    '.listenEngagement button.sc-button-like',
    '.soundActions button.sc-button-like',
    'button.sc-button-like',
    'button[aria-label*="ike"]',
    'button[title*="ike"]',
  ];
  for (const sel of selectors) {
    const b = document.querySelector(sel);
    if (!b) continue;
    const pressed = b.getAttribute('aria-pressed');
    const cls = b.className || '';
    const label = (b.getAttribute('aria-label') || b.getAttribute('title') || '');
    let liked = null;
    if (pressed === 'true') liked = true;
    else if (pressed === 'false') liked = false;
    else if (/sc-button-selected/.test(cls)) liked = true;
    else if (/^unlike/i.test(label.trim())) liked = true;
    else if (/^like/i.test(label.trim())) liked = false;
    return { found: true, selector: sel, liked, label: label.trim() };
  }
  return { found: false };
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


def row_to_track(row: dict) -> Track | None:
    path = str(row.get("path") or "").strip("/")
    if not path or "/" not in path:
        return None
    owner, _, slug = path.partition("/")
    if owner.lower() in _NOT_A_TRACK or slug.lower() in _NOT_A_SLUG:
        return None
    title = str(row.get("title") or "").strip()
    if not title:
        return None
    user = str(row.get("user") or "").strip()
    return Track(
        provider=SOUNDCLOUD,
        id=web_track_id(path),
        title=title,
        artists=(user,) if user else (),
        duration_ms=parse_duration(str(row.get("duration") or "")),
        isrc=None,  # not exposed in the page; matching copes without it
        url=f"{HOME}/{path}",
        raw_title=title,
        # The uploader here is whoever posted it, so prefer the artist parsed
        # out of "Artist - Title" when scoring.
        extra={"artist_source": "uploader", "permalink": path},
    )


class SoundCloudWebProvider:
    name = SOUNDCLOUD

    def __init__(
        self,
        page: Page,
        *,
        memo: Memo | None = None,
        max_scrolls: int = 400,
        settle_ms: int = 450,
        write_pause_s: float = 1.5,
        verify_writes: bool = True,
    ) -> None:
        self.page = page
        self.memo = memo or DictMemo()
        self.max_scrolls = max_scrolls
        self.settle_ms = settle_ms
        self.write_pause_s = write_pause_s
        self.verify_writes = verify_writes
        self.failures: dict[str, str] = {}

    # -- reading -----------------------------------------------------------

    def _guard_signed_out(self) -> None:
        url = self.page.current_url()
        if "/signin" in url or "/login" in url:
            raise AuthError(
                "SoundCloud redirected to the sign-in page, so the saved "
                "browser session has expired.\n"
                "Re-run: likesync login soundcloud --web"
            )

    def _read_listing(self, url: str, *, what: str, scroll: bool = True) -> list[Track]:
        self.page.goto(url)
        self._guard_signed_out()
        self.page.wait(1200)
        if scroll:
            settled = self.page.scroll_to_end(
                max_scrolls=self.max_scrolls, settle_ms=self.settle_ms
            )
            if not settled:
                # Returning a partial list would read as a mass unlike.
                raise ProviderError(
                    f"soundcloud-web: reached the {self.max_scrolls}-scroll "
                    f"limit while loading {what} and the page was still "
                    "growing, so the list is incomplete. Raise "
                    "soundcloud.max_scrolls and re-run."
                )
        rows = self.page.extract(_EXTRACT_ROWS) or []
        tracks = [t for t in (row_to_track(r) for r in rows) if t]
        if not tracks and rows:
            raise ProviderError(
                f"soundcloud-web: found {len(rows)} rows on {what} but could "
                "not read a single track from them. Their markup has probably "
                "changed; run `likesync probe soundcloud -v`."
            )
        # Deduplicate: a track can appear twice in a listing.
        unique: dict[str, Track] = {}
        for track in tracks:
            unique.setdefault(track.id, track)
        log.info("soundcloud-web: read %d track(s) from %s", len(unique), what)
        return list(unique.values())

    def liked(self) -> list[Track]:
        return self._read_listing(f"{HOME}/you/likes", what="your likes")

    def playlist_tracks(self, url: str) -> list[Track]:
        if not url.startswith(("http://", "https://")):
            url = f"{HOME}/{url.lstrip('/')}"
        return self._read_listing(url, what="the inbox playlist")

    def search(self, query: str, limit: int = 10) -> list[Track]:
        url = f"{HOME}/search/sounds?q={urllib.parse.quote(query)}"
        # Search results are ranked, so the first page is all that is useful.
        tracks = self._read_listing(url, what=f"search {query!r}", scroll=False)
        return tracks[:limit]

    def search_isrc(self, isrc: str, limit: int = 5) -> list[Track]:
        # SoundCloud has no ISRC search; ISRC only ever confirms a pair when
        # both sides already publish one, and web mode never has it.
        return []

    # -- writing -----------------------------------------------------------

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

    def _set_like(self, track_id: str, *, want_liked: bool) -> bool:
        """Put one track into the wanted state. Idempotent."""
        self.page.goto(track_url(track_id))
        self._guard_signed_out()
        self.page.wait(900)

        state = self.page.extract(_LIKE_STATE) or {}
        if not state.get("found"):
            raise ProviderError(
                "no like button on the track page (deleted, private or "
                "region-blocked track, or changed markup)"
            )
        if state.get("liked") is want_liked:
            return True  # already where we want it

        selector = state.get("selector") or "button.sc-button-like"
        if not self.page.click(selector):
            raise ProviderError(f"could not click the like button ({selector})")
        self.page.wait(1200)

        if not self.verify_writes:
            return True
        after = self.page.extract(_LIKE_STATE) or {}
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

    # -- diagnostics -------------------------------------------------------

    def probe(self) -> dict[str, Any]:
        """Read-only check that the session works and the extractor can see. """
        report: dict[str, Any] = {"checks": []}

        def record(name: str, **fields: Any) -> None:
            report["checks"].append({"check": name, **fields})

        self.page.goto(f"{HOME}/you/likes")
        self.page.wait(1500)
        url = self.page.current_url()
        signed_in = "/signin" not in url and "/login" not in url
        record("signed in", ok=signed_in, landed_on=url)
        if not signed_in:
            report["verdict"] = "not signed in - run: likesync login soundcloud --web"
            return report

        rows = self.page.extract(_EXTRACT_ROWS) or []
        tracks = [t for t in (row_to_track(r) for r in rows) if t]
        record(
            "read likes page (first screen, no scrolling)",
            ok=bool(tracks), rows_found=len(rows), tracks_parsed=len(tracks),
            sample=[t.display() for t in tracks[:3]],
        )

        found = self.page.extract(
            "() => { const r = document.querySelectorAll("
            "'button.sc-button-like').length; return r; }"
        )
        record("like buttons visible on the listing", count=found)

        self.page.goto(f"{HOME}/search/sounds?q=burial%20archangel")
        self.page.wait(1500)
        s_rows = self.page.extract(_EXTRACT_ROWS) or []
        s_tracks = [t for t in (row_to_track(r) for r in s_rows) if t]
        record(
            "search works", ok=bool(s_tracks), rows_found=len(s_rows),
            sample=[t.display() for t in s_tracks[:3]],
        )

        report["verdict"] = (
            "ready" if tracks and s_tracks
            else "signed in, but the page extractor needs updating - send this report"
        )
        report["note"] = (
            "Write actions are not probed: liking a track changes your account. "
            "Use `likesync plan` to see what a real run would do."
        )
        return report
