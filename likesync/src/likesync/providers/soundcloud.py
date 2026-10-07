"""SoundCloud provider.

Two moving parts to defend against:

* Track ids are migrating from integers to URN strings
  (``soundcloud:tracks:123456``). Everything here keeps ids as strings and
  derives a path segment at call time.
* The like/unlike routes have moved. ``POST|DELETE /likes/tracks/{id}`` is
  current; ``PUT|DELETE /me/favorites/{id}`` is the older pair and is still
  what some clients are served. The first successful variant is remembered.
"""

from __future__ import annotations

import logging
import urllib.parse
from collections.abc import Sequence
from typing import Any

from ..errors import ProviderError
from ..httpc import ApiClient
from ..models import SOUNDCLOUD, Track
from ..oauth import Authenticator
from .base import DictMemo, Memo, systemic

log = logging.getLogger("likesync.soundcloud")

MODE_KEY = "soundcloud_write_mode"
PAGE_SIZE = 50
_FALLBACK_STATUSES = frozenset({403, 404, 405, 410, 422})


def path_id(track_id: str) -> str:
    """Path segment for a track id, URN or numeric."""
    tid = str(track_id)
    if tid.startswith("soundcloud:"):
        tail = tid.rsplit(":", 1)[-1]
        if tail.isdigit():
            return tail
    return urllib.parse.quote(tid, safe=":")


class SoundCloudProvider:
    name = SOUNDCLOUD

    def __init__(
        self,
        client: ApiClient,
        auth: Authenticator,
        *,
        memo: Memo | None = None,
        write_mode: str = "auto",
    ) -> None:
        self.client = client
        self.auth = auth
        self.memo = memo or DictMemo()
        self.configured_mode = write_mode

    # -- reading -----------------------------------------------------------

    def liked(self) -> list[Track]:
        return self._collect(
            "/me/likes/tracks",
            params={"limit": PAGE_SIZE, "linked_partitioning": True},
        )

    def search(self, query: str, limit: int = 10) -> list[Track]:
        response = self.client.request(
            "GET",
            "/tracks",
            params={
                "q": query,
                "limit": max(1, min(limit, 200)),
                "linked_partitioning": True,
            },
        )
        if response.status in (404, 422):
            return []
        payload = response.raise_for_status().json()
        return self._parse_page(payload)[0]

    def search_isrc(self, isrc: str, limit: int = 5) -> list[Track]:
        # SoundCloud search has no ISRC filter; matching uses ISRC only when
        # both sides already expose one.
        return []

    def _collect(self, path: str, params: dict[str, Any] | None) -> list[Track]:
        tracks: list[Track] = []
        seen_urls: set[str] = set()
        next_path: str | None = path
        pages = 0
        while next_path:
            response = self.client.request("GET", next_path, params=params)
            payload = response.raise_for_status().json()
            page, next_href = self._parse_page(payload)
            tracks.extend(page)
            params = None  # next_href carries its own query
            if not next_href or next_href in seen_urls:
                break
            seen_urls.add(next_href)
            next_path = next_href
            pages += 1
            if pages > 2000:  # pragma: no cover - runaway guard
                raise ProviderError("soundcloud: pagination did not terminate")
        return tracks

    def _parse_page(self, payload: Any) -> tuple[list[Track], str | None]:
        if isinstance(payload, list):
            raw_items, next_href = payload, None
        elif isinstance(payload, dict):
            raw_items = payload.get("collection") or []
            next_href = payload.get("next_href") or None
        else:
            raise ProviderError(f"soundcloud: unexpected payload {type(payload)}")
        out = []
        for raw in raw_items:
            # Some collections wrap the track, e.g. {"track": {...}}.
            if isinstance(raw, dict) and "track" in raw and isinstance(raw["track"], dict):
                raw = raw["track"]
            track = self._parse(raw)
            if track:
                out.append(track)
        return out, next_href

    def _parse(self, raw: Any) -> Track | None:
        if not isinstance(raw, dict):
            return None
        if raw.get("kind") not in (None, "track"):
            return None
        urn = raw.get("urn")
        raw_id = raw.get("id")
        if not urn and raw_id in (None, ""):
            return None
        track_id = str(urn or raw_id)

        meta = raw.get("publisher_metadata") or {}
        user = raw.get("user") or {}
        artists: list[str] = []
        for candidate in (meta.get("artist"), user.get("username")):
            name = str(candidate).strip() if candidate else ""
            if name and name not in artists:
                artists.append(name)
        # The uploader is frequently a label or promo channel, so note when
        # that is all we have: matching then prefers the artist parsed out of
        # the title instead.
        artist_source = "publisher" if meta.get("artist") else "uploader"

        title = str(raw.get("title") or "")
        # full_duration is the real length; `duration` is the preview for
        # snippet-only uploads.
        duration = raw.get("full_duration") or raw.get("duration")
        isrc = meta.get("isrc") or None

        return Track(
            provider=SOUNDCLOUD,
            id=track_id,
            title=title,
            artists=tuple(artists),
            duration_ms=int(duration) if duration else None,
            isrc=str(isrc).strip() if isrc else None,
            url=raw.get("permalink_url") or None,
            raw_title=title,
            extra={
                "numeric_id": str(raw_id) if raw_id is not None else "",
                "artist_source": artist_source,
            },
        )

    # -- writing -----------------------------------------------------------

    @property
    def write_mode(self) -> str:
        if self.configured_mode in ("likes", "favorites"):
            return self.configured_mode
        return self.memo.get_meta(MODE_KEY) or "auto"

    def _remember_mode(self, mode: str) -> None:
        if self.configured_mode == "auto":
            self.memo.set_meta(MODE_KEY, mode)

    def like(self, track_ids: Sequence[str]) -> list[str]:
        return self._write_many(track_ids, remove=False)

    def unlike(self, track_ids: Sequence[str]) -> list[str]:
        return self._write_many(track_ids, remove=True)

    def _write_many(self, track_ids: Sequence[str], *, remove: bool) -> list[str]:
        """Apply one id at a time; SoundCloud has no batch like endpoint."""
        done: list[str] = []
        self.failures: dict[str, str] = {}
        for track_id in track_ids:
            try:
                if self._write_one(track_id, remove=remove):
                    done.append(track_id)
            except Exception as exc:
                if systemic(exc):
                    raise
                log.warning("soundcloud: %s failed for %s: %s",
                            "unlike" if remove else "like", track_id, exc)
                self.failures[track_id] = str(exc)
        return done

    def _write_one(self, track_id: str, *, remove: bool) -> bool:
        segment = path_id(track_id)
        mode = self.write_mode

        if mode in ("auto", "likes"):
            verb = "DELETE" if remove else "POST"
            response = self.client.request(verb, f"/likes/tracks/{segment}")
            if response.ok or (remove and response.status == 404):
                self._remember_mode("likes")
                return True
            if mode == "likes" or response.status not in _FALLBACK_STATUSES:
                raise ProviderError(
                    f"soundcloud: {verb} /likes/tracks/{segment} returned "
                    f"{response.status}: {response.text[:200]}",
                    status=response.status,
                )
            log.info(
                "soundcloud: /likes/tracks returned %d; trying /me/favorites",
                response.status,
            )

        verb = "DELETE" if remove else "PUT"
        response = self.client.request(verb, f"/me/favorites/{segment}")
        if response.ok or (remove and response.status == 404):
            self._remember_mode("favorites")
            return True
        raise ProviderError(
            f"soundcloud: {verb} /me/favorites/{segment} returned "
            f"{response.status}: {response.text[:200]}",
            status=response.status,
        )
