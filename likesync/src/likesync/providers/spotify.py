"""Spotify provider.

February 2026 moved library writes from ``PUT/DELETE /v1/me/tracks`` (JSON
``ids`` body, 50 per call) to ``PUT/DELETE /v1/me/library`` (``uris`` query
parameter, 40 per call). Client ids created after the cutover get 403 on the
old routes, and some older apps were still being served them, so the write path
probes once and remembers which variant this client is allowed to use.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ..errors import ProviderError
from ..httpc import ApiClient
from ..models import SPOTIFY, Track
from ..oauth import Authenticator
from .base import DictMemo, Memo, chunked, systemic

log = logging.getLogger("likesync.spotify")

LIBRARY_BATCH = 40   # unified /me/library endpoint
TRACKS_BATCH = 50    # legacy /me/tracks endpoint
MODE_KEY = "spotify_write_mode"
# Statuses that mean "this endpoint is not available to this client".
_FALLBACK_STATUSES = frozenset({403, 404, 405, 410})


class SpotifyProvider:
    name = SPOTIFY

    def __init__(
        self,
        client: ApiClient,
        auth: Authenticator,
        *,
        memo: Memo | None = None,
        write_mode: str = "auto",
        market: str = "",
    ) -> None:
        self.client = client
        self.auth = auth
        self.memo = memo or DictMemo()
        self.configured_mode = write_mode
        self.market = market

    # -- reading -----------------------------------------------------------

    def liked(self) -> list[Track]:
        tracks: list[Track] = []
        path: str | None = "/me/tracks"
        params: dict[str, Any] | None = {"limit": 50}
        if self.market:
            params["market"] = self.market
        pages = 0
        while path:
            payload = (
                self.client.request("GET", path, params=params)
                .raise_for_status()
                .json()
            ) or {}
            for item in payload.get("items") or []:
                raw = item.get("track") or {}
                track = self._parse(raw, added_at=item.get("added_at"))
                if track:
                    tracks.append(track)
            path = payload.get("next")
            params = None  # `next` is a fully-formed URL
            pages += 1
            if pages > 2000:  # pragma: no cover - runaway guard
                raise ProviderError("spotify: pagination did not terminate")
        return tracks

    def search(self, query: str, limit: int = 10) -> list[Track]:
        params = {"q": query, "type": "track", "limit": max(1, min(limit, 50))}
        if self.market:
            params["market"] = self.market
        payload = self.client.request("GET", "/search", params=params)
        if payload.status == 404:
            return []
        data = payload.raise_for_status().json() or {}
        items = ((data.get("tracks") or {}).get("items")) or []
        return [t for t in (self._parse(i) for i in items) if t]

    def search_isrc(self, isrc: str, limit: int = 5) -> list[Track]:
        return self.search(f"isrc:{isrc}", limit=limit)

    def _parse(self, raw: dict, *, added_at: str | None = None) -> Track | None:
        if not raw:
            return None
        track_id = raw.get("id")
        # Local files appear in Liked Songs with a null id and cannot be
        # addressed by the API at all.
        if not track_id or raw.get("is_local"):
            return None
        return Track(
            provider=SPOTIFY,
            id=str(track_id),
            title=str(raw.get("name") or ""),
            artists=tuple(
                str(a.get("name")) for a in (raw.get("artists") or []) if a.get("name")
            ),
            duration_ms=raw.get("duration_ms"),
            isrc=((raw.get("external_ids") or {}).get("isrc") or None),
            url=((raw.get("external_urls") or {}).get("spotify") or None),
            album=((raw.get("album") or {}).get("name") or None),
            added_at=added_at,
        )

    # -- writing -----------------------------------------------------------

    @property
    def write_mode(self) -> str:
        if self.configured_mode in ("library", "tracks"):
            return self.configured_mode
        return self.memo.get_meta(MODE_KEY) or "auto"

    def _remember_mode(self, mode: str) -> None:
        if self.configured_mode == "auto":
            self.memo.set_meta(MODE_KEY, mode)

    def like(self, track_ids: Sequence[str]) -> list[str]:
        return self._write(track_ids, remove=False)

    def unlike(self, track_ids: Sequence[str]) -> list[str]:
        return self._write(track_ids, remove=True)

    def _write(self, track_ids: Sequence[str], *, remove: bool) -> list[str]:
        ids = [str(i) for i in track_ids if i]
        if not ids:
            return []
        done: list[str] = []
        self.failures: dict[str, str] = {}
        mode = self.write_mode
        size = TRACKS_BATCH if mode == "tracks" else LIBRARY_BATCH
        for batch in chunked(ids, size):
            try:
                done.extend(self._write_batch(batch, remove=remove, mode=mode))
            except Exception as exc:
                if systemic(exc) or len(batch) == 1:
                    if systemic(exc):
                        raise
                    log.warning("spotify: %s failed for %s: %s",
                                "unlike" if remove else "like", batch[0], exc)
                    self.failures[batch[0]] = str(exc)
                else:
                    # One unacceptable id fails the whole batch, so find out
                    # which rather than dropping 40 tracks.
                    log.info("spotify: batch of %d failed (%s); retrying singly",
                             len(batch), exc)
                    for single in batch:
                        try:
                            done.extend(
                                self._write_batch([single], remove=remove,
                                                  mode=self.write_mode)
                            )
                        except Exception as inner:
                            if systemic(inner):
                                raise
                            log.warning("spotify: %s failed for %s: %s",
                                        "unlike" if remove else "like", single, inner)
                            self.failures[single] = str(inner)
            mode = self.write_mode  # a probe may have settled the mode
        return done

    def _write_batch(
        self, batch: list[str], *, remove: bool, mode: str
    ) -> list[str]:
        verb = "DELETE" if remove else "PUT"

        if mode in ("auto", "library"):
            uris = ",".join(f"spotify:track:{i}" for i in batch)
            response = self.client.request(verb, "/me/library", params={"uris": uris})
            if response.ok:
                self._remember_mode("library")
                return batch
            if mode == "library" or response.status not in _FALLBACK_STATUSES:
                raise ProviderError(
                    f"spotify: {verb} /me/library returned {response.status}: "
                    f"{response.text[:300]}",
                    status=response.status,
                )
            log.info(
                "spotify: /me/library unavailable (%d); trying legacy /me/tracks",
                response.status,
            )

        # Legacy per-type route. Batches are capped at LIBRARY_BATCH above,
        # which is within the legacy limit of 50, so no re-chunking is needed.
        response = self.client.request(verb, "/me/tracks", json_body={"ids": batch})
        if response.ok:
            self._remember_mode("tracks")
            return batch
        hint = ""
        if response.status == 403:
            hint = (
                "\nA 403 here usually means this client id is subject to the "
                "February 2026 endpoint restrictions and /me/library is the only "
                "write path, or that the app owner's Spotify Premium lapsed."
            )
        raise ProviderError(
            f"spotify: {verb} /me/tracks returned {response.status}: "
            f"{response.text[:300]}{hint}",
            status=response.status,
        )
