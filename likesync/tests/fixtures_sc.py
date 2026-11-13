"""SoundCloud pages and page data for the web-provider tests.

Modelled on the live site as it was inspected in November 2026: the class
names, the row structure, the like-button nesting and the shape of the track
lists a page loads are all taken from real pages, cut down to what matters.
Three details of the real thing that a naive reader gets wrong are kept on
purpose:

* rows show **no duration** (the site draws it on the waveform);
* a liked **playlist** sits in the same list as liked tracks and lists its own
  tracks inside its row;
* a track page has like buttons for **related tracks** as well as its own.
"""

from __future__ import annotations

import json

# --------------------------------------------------------------------------
# markup
# --------------------------------------------------------------------------


def _row(path: str, title: str, user_path: str, user: str) -> str:
    return f"""
  <li class="soundList__item">
    <div class="sound streamContext">
      <div class="sound__body">
        <div class="soundTitle sc-clearfix">
          <a class="soundTitle__username sc-link-secondary" href="{user_path}">
            <span class="soundTitle__usernameText">{user}</span></a>
          <a class="sc-link-primary soundTitle__title sc-link-dark" href="{path}">
            <span>{title}</span></a>
          <time class="relativeTime" datetime="2023-02-02T17:00:10.000Z">3 years ago</time>
        </div>
        <div class="soundActions sc-button-toolbar soundActions__medium">
          <button class="sc-button-like sc-button-secondary sc-button"
                  aria-label="Like" title="Like">Like</button>
        </div>
        <ul class="soundStats"><li>1,418,465 plays</li><li>38.3K</li></ul>
      </div>
    </div>
  </li>"""


_PLAYLIST_ROW = """
  <li class="soundList__item">
    <div class="sound playlist streamContext">
      <div class="sound__body">
        <div class="soundTitle sc-clearfix">
          <a class="soundTitle__username" href="/flume">Flume</a>
          <a class="sc-link-primary soundTitle__title" href="/flume/sets/quits-ep">Quits EP</a>
        </div>
        <ul class="compactTrackList__list">
          <li class="compactTrackList__item">
            <a href="/flume/quits">Quits</a></li>
          <li class="compactTrackList__item">
            <a href="/flume/amber">Amber</a></li>
        </ul>
      </div>
    </div>
  </li>"""

LIKES_PAGE = f"""
<!doctype html><html><body>
<header class="header">
  <nav><a href="/discover">Discover</a><a href="/you/library">Library</a></nav>
</header>
<div class="soundList lazyLoadingList">
<ul class="lazyLoadingList__list sc-list-nostyle">
  {_row("/burial/archangel", "Archangel", "/burial", "Hyperdub")}
  {_row("/fredagain/delilah-skrillex-remix", "Fred again.. - Delilah (Skrillex Remix)",
        "/fredagain", "Fred again..")}
  {_PLAYLIST_ROW}
  {_row("/unsound/unsound-podcast-109", "Unsound Podcast 109: Bobby Beethoven",
        "/unsound", "Unsound")}
</ul>
</div>
</body></html>
"""

# No SoundCloud class names at all: the structural fallback has to carry it.
GENERIC_PAGE = """
<!doctype html><html><body>
<ul>
  <li><a href="/floating-points/last-bloom">Last Bloom</a>
      <a href="/floating-points">Ninja Tune</a><span>6:30</span></li>
  <li><a href="/bicep/glue">Glue</a>
      <a href="/bicep">Ninja Tune</a><span>4:31</span></li>
  <li><a href="/m83/midnight-city">Midnight City</a>
      <a href="/m83">M83</a><span>4:04</span></li>
</ul>
</body></html>
"""

SEARCH_PAGE = """
<!doctype html><html><body>
<ul class="lazyLoadingList__list">
  <li class="searchList__item"><div class="sound streamContext"><div class="soundTitle">
    <a class="soundTitle__username" href="/burial-uk-1">Burial Uk</a>
    <a class="soundTitle__title" href="/burial-uk-1/archangel">Archangel</a>
  </div></div></li>
  <li class="searchList__item"><div class="sound streamContext"><div class="soundTitle">
    <a class="soundTitle__username" href="/trashpixels">trashpixels</a>
    <a class="soundTitle__title" href="/trashpixels/burial-archangel">Burial-Archangel</a>
  </div></div></li>
  <li class="searchList__item"><div class="sound streamContext"><div class="soundTitle">
    <a class="soundTitle__username" href="/musicofpagan">Pagan</a>
    <a class="soundTitle__title" href="/musicofpagan/burial-archangel-pagan-edit">
      Burial - Archangel (Pagan Edit)</a>
  </div></div></li>
</ul>
</body></html>
"""

EMPTY_PAGE = "<!doctype html><html><body><p>Nothing here yet.</p></body></html>"

_TOGGLE = """
  const liked = this.getAttribute('aria-label') === 'Unlike';
  this.setAttribute('aria-label', liked ? 'Like' : 'Unlike');
  this.setAttribute('title', liked ? 'Like' : 'Unlike');
  this.className = 'sc-button-like sc-button' + (liked ? '' : ' sc-button-selected');
"""


def _like_button(liked: bool, size: str = "medium") -> str:
    label = "Unlike" if liked else "Like"
    selected = " sc-button-selected" if liked else ""
    return (
        f'<button class="sc-button-like sc-button{selected} sc-button-{size}" '
        f'aria-label="{label}" title="{label}" onclick="{_TOGGLE}">{label}</button>'
    )


_SIDEBAR = f"""
  <div class="sidebarModule">
    <ul class="soundBadgeList compact">
      <li class="soundBadgeList__item"><div class="soundBadge compact">
        <a href="/someone/a-related-track">A related track</a>
        <div class="soundBadge__actions"><div class="soundActions soundActions__small">
          {_like_button(False, "small")}</div></div></div></li>
      <li class="soundBadgeList__item"><div class="soundBadge compact">
        <a href="/someone/another-related-track">Another related track</a>
        <div class="soundBadge__actions"><div class="soundActions soundActions__small">
          {_like_button(False, "small")}</div></div></div></li>
    </ul>
  </div>"""


def track_page(*, liked: bool, own_button: bool = True, scoped: bool = True,
               sidebar_first: bool = False) -> str:
    """A track page whose like buttons really toggle when clicked.

    As on the real site, the track's own button lives inside
    ``.sound.listenContext`` and the sidebar holds buttons for other tracks.
    """
    scope = "sound listenContext visualSound" if scoped else "sound visualSound"
    own = f"""
  <div class="listenContent__inner">
    <div class="{scope}"><div class="sound__body"><div class="sound__footer">
      <div class="sound__soundActions">
        <div class="soundActions sc-button-toolbar soundActions__medium">
          {_like_button(liked) if own_button else "<p>no like control here</p>"}
        </div></div></div></div></div>
  </div>"""
    parts = [_SIDEBAR, own] if sidebar_first else [own, _SIDEBAR]
    return f"<!doctype html><html><body>{''.join(parts)}</body></html>"


AMBIGUOUS_TRACK_PAGE = f"""
<!doctype html><html><body>
  <div class="hero">{_like_button(False)}</div>
  <div class="footerBar">{_like_button(False)}</div>
</body></html>
"""

LIKED_BUTTONS = "() => Array.from(document.querySelectorAll('button.sc-button-like'))" \
                ".map(b => b.getAttribute('aria-label'))"


# --------------------------------------------------------------------------
# the data a page loads for itself
# --------------------------------------------------------------------------

LIKES_PATH = "/users/42/likes"


def raw_track(number: int, owner: str, slug: str, title: str, user: str, ms: int,
              *, isrc: str | None = None, artist: str | None = None) -> dict:
    return {
        "kind": "track",
        "id": number,
        "urn": f"soundcloud:tracks:{number}",
        "title": title,
        "user": {"username": user, "permalink": owner},
        "duration": 30_000,          # the preview length
        "full_duration": ms,         # the real one
        "permalink_url": f"https://soundcloud.com/{owner}/{slug}",
        "publisher_metadata": {"isrc": isrc, "artist": artist} if (isrc or artist) else None,
    }


ARCHANGEL = raw_track(101, "burial", "archangel", "Archangel", "Hyperdub", 237_000,
                      isrc="GBAYE0601498", artist="Burial")
DELILAH = raw_track(102, "fredagain", "delilah-skrillex-remix",
                    "Fred again.. - Delilah (Skrillex Remix)", "Fred again..", 215_000)
PODCAST = raw_track(103, "unsound", "unsound-podcast-109",
                    "Unsound Podcast 109: Bobby Beethoven", "Unsound", 5_339_481)
QUITS = raw_track(201, "flume", "quits", "Quits", "Flume", 201_000)
AMBER = raw_track(202, "flume", "amber", "Amber", "Flume", 188_000)


def like(raw: dict) -> dict:
    return {"created_at": "2026-01-01T00:00:00Z", "kind": "like", "track": raw}


def liked_playlist(*tracks: dict) -> dict:
    return {
        "created_at": "2026-01-01T00:00:00Z",
        "kind": "like",
        "playlist": {"kind": "playlist", "id": 900, "title": "Quits EP",
                     "permalink_url": "https://soundcloud.com/flume/sets/quits-ep",
                     "tracks": list(tracks)},
    }


def likes_batch(*entries: dict, more: bool, path: str = LIKES_PATH) -> tuple[str, dict]:
    next_href = f"https://api-v2.soundcloud.com{path}?offset=abc&limit=24" if more else None
    return path, {"collection": list(entries), "next_href": next_href, "query_urn": None}


def search_batch(*tracks: dict) -> tuple[str, dict]:
    return "/search/tracks", {"collection": list(tracks), "next_href": None,
                              "total_results": len(tracks)}


def me(permalink: str = "my-name") -> tuple[str, dict]:
    return "/me", {"permalink_url": f"https://soundcloud.com/{permalink}"}


def playlist_page(members: list[dict], *, hydrated: int) -> str:
    """A playlist page: details for the first few members, bare ids for the rest."""
    tracks = [m if i < hydrated else {"id": m["id"], "kind": "track"}
              for i, m in enumerate(members)]
    data = [{"hydratable": "user", "data": {"kind": "user"}},
            {"hydratable": "playlist", "data": {"kind": "playlist", "tracks": tracks}}]
    rows = "".join(
        f'<li class="trackList__item"><div class="trackItem">'
        f'<a class="trackItem__username" href="/{m["user"]["permalink"]}">{m["user"]["username"]}</a>'
        f'<a class="trackItem__trackTitle" href="{m["permalink_url"].replace("https://soundcloud.com", "")}'
        f'?in=me/sets/sync-me">{m["title"]}</a></div></li>'
        for m in members
    )
    return (
        "<!doctype html><html><body>"
        f"<script>window.__sc_hydration = {json.dumps(data)};</script>"
        f'<ul class="trackList__list">{rows}</ul>'
        "</body></html>"
    )
